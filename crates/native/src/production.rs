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
    time::{Instant, sleep, timeout_at},
};
use uuid::Uuid;

mod blender_original;
mod mlt_mezzanine;
mod mlt_multisegment_av;
mod mlt_timeline;
#[cfg(test)]
mod path_boundary_tests;
mod receipt_custody;
mod recovery;
pub use blender_original::{
    BlenderOriginalEdit, BlenderOriginalFile, BlenderOriginalJournal, BlenderOriginalPhase,
    BlenderOriginalRequest,
};
pub use mlt_mezzanine::{MltPreparedMezzanines, MltVerifiedMezzanine};
pub use mlt_multisegment_av::{MltMultisegmentAvMasterEvidence, MltMultisegmentAvMasterRequest};
pub use mlt_timeline::MltVerifiedLosslessTimeline;
pub use recovery::{CrossAppRecoveryReport, RecoveryBlenderProof, StageDisposition};

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
    "driver.blender.scene.inspect",
    "driver.blender.semantic.objects",
    "driver.blender.semantic.relations",
    "driver.blender.semantic.rename",
    "driver.blender.semantic.custom.list",
    "driver.blender.semantic.custom.set",
    "driver.blender.semantic.object.create",
    "driver.blender.semantic.datablock.create",
    "driver.blender.semantic.relation.set",
    "driver.blender.semantic.relation.unlink",
    "driver.blender.semantic.property.get",
    "driver.blender.semantic.property.set",
    "driver.blender.file.save",
    "driver.blender.file.open",
];

const MANIM_COMMANDS: &[&str] = &[
    "driver.manim-community.render.start",
    "driver.manim-community.camera.render.start",
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
    #[cfg(test)]
    execute_calls: std::sync::Arc<std::sync::atomic::AtomicUsize>,
}

impl ProductionClient {
    pub fn new(connection: ProductionConnection) -> NativeResult<Self> {
        connection.validate()?;
        Ok(Self {
            connection,
            #[cfg(test)]
            execute_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        })
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

        let deadline = Instant::now() + Duration::from_secs(RUNTIME_PROBE_DEADLINE_SECS);
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
        // One deadline covers process exit and EOF on both pipes. A descendant
        // may retain a writer after the direct CLI has exited. Keep all readers
        // in this future so timeout or caller cancellation closes them.
        let captured = timeout_at(deadline, async {
            tokio::try_join!(
                async {
                    child
                        .wait()
                        .await
                        .map_err(|_| backend("Semwright CLI version probe failed"))
                },
                async {
                    read_bounded(stdout)
                        .await
                        .map_err(|_| backend("Semwright CLI version output could not be read"))
                },
                async {
                    read_bounded(stderr)
                        .await
                        .map_err(|_| backend("Semwright CLI version diagnostics could not be read"))
                },
            )
        })
        .await;
        let (status, stdout, stderr) = match captured {
            Ok(result) => result?,
            Err(_) => {
                // Dropping the owned child retains kill_on_drop semantics,
                // without an additional unbounded kill/wait after the deadline.
                return Err(Error::new(
                    ErrorCode::Timeout,
                    "Semwright CLI version probe timed out",
                ));
            }
        };
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
        #[cfg(test)]
        let _ = self
            .execute_calls
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);

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

        let budget_secs = if command.starts_with("driver.mlt-video.")
            || command.starts_with("driver.blender.")
            || command.starts_with("driver.manim-community.")
        {
            MLT_DEADLINE_SECS
        } else {
            DEFAULT_DEADLINE_SECS
        };
        let deadline = Instant::now() + Duration::from_secs(budget_secs);
        let mut child = child.spawn().map_err(|_| {
            Error::new(ErrorCode::Unavailable, "Semwright CLI could not be started")
        })?;
        // Once the child starts, a transport failure cannot establish whether a
        // mutation ran. A typed Broker error retains its own outcome below.
        let post_spawn_error = |error: Error| {
            if mutation { error.uncertain() } else { error }
        };
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| post_spawn_error(backend("Semwright CLI stdout unavailable")))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| post_spawn_error(backend("Semwright CLI stderr unavailable")))?;
        // Joining owned reader futures avoids detached tasks on every error
        // and cancellation path. Process exit never starts a new output budget.
        let captured = timeout_at(deadline, async {
            tokio::try_join!(
                async {
                    child
                        .wait()
                        .await
                        .map_err(|_| post_spawn_error(backend("Semwright CLI process failed")))
                },
                async {
                    read_bounded(stdout).await.map_err(|_| {
                        post_spawn_error(backend("Semwright CLI output could not be read"))
                    })
                },
                async {
                    read_bounded(stderr).await.map_err(|_| {
                        post_spawn_error(backend("Semwright CLI diagnostics could not be read"))
                    })
                },
            )
        })
        .await;
        let (status, stdout, stderr) = match captured {
            Ok(result) => result?,
            Err(_) => {
                let error =
                    Error::new(ErrorCode::Timeout, "Canonical production command timed out");
                return Err(post_spawn_error(error));
            }
        };
        if stdout.len() > MAX_OUTPUT_BYTES || stderr.len() > MAX_OUTPUT_BYTES {
            return Err(post_spawn_error(Error::new(
                ErrorCode::ResourceExhausted,
                "Semwright CLI response exceeded the transport budget",
            )));
        }

        let envelope: Envelope = serde_json::from_slice(&stdout).map_err(|_| {
            post_spawn_error(backend(
                "Semwright CLI returned an invalid response envelope",
            ))
        })?;
        if envelope.command != command {
            return Err(post_spawn_error(backend(
                "Semwright CLI response command does not match the request",
            )));
        }
        if !status.success() || !envelope.ok {
            let error = envelope.error.unwrap_or_else(|| {
                post_spawn_error(backend(
                    "Canonical production command failed without a typed error",
                ))
            });
            return Err(error);
        }
        let provenance = envelope.execution.provenance.as_ref().ok_or_else(|| {
            post_spawn_error(backend("Canonical production response has no provenance"))
        })?;
        if provenance.provider != expected.provider
            || provenance.source != expected.source
            || (expected.provider_generation_required && provenance.provider_generation.is_none())
            || !is_sha256(&provenance.descriptor_sha256)
        {
            return Err(post_spawn_error(backend(
                "Canonical response provenance does not match the expected authority",
            )));
        }
        let value = envelope.data.ok_or_else(|| {
            post_spawn_error(backend("Canonical production response has no data"))
        })?;
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

/// A failed observation after an acknowledged start cannot establish the
/// render's outcome. Retain the original typed diagnostic and the admitted
/// start command; the caller must reconcile the existing request and job.
fn unconfirmed_started_manim_render(error: Error, start_command: &'static str) -> Error {
    error.uncertain().recipe_progress(1, start_command)
}

fn require_acknowledged_manim_observation<'a>(
    observation: &'a Value,
    acknowledged_job_ref: &str,
    acknowledged_generation: u64,
    start_command: &'static str,
    observation_kind: &'static str,
) -> NativeResult<&'a Value> {
    let data = response_data(observation)
        .map_err(|error| unconfirmed_started_manim_render(error, start_command))?;
    let generation = observation
        .pointer("/result/execution/provenance/provider_generation")
        .and_then(Value::as_u64);
    if data.get("job_ref").and_then(Value::as_str) != Some(acknowledged_job_ref)
        || generation != Some(acknowledged_generation)
    {
        return Err(unconfirmed_started_manim_render(
            backend(format!(
                "Manim {observation_kind} does not identify the acknowledged native job and generation"
            )),
            start_command,
        ));
    }
    Ok(data)
}

struct PreparedManimRender {
    start_command: &'static str,
    plan_value: Value,
    plan_sha256: String,
    profile: ManimRenderProfile,
    requires_camera_getters: bool,
}

fn validate_manim_request_id(request_id: &str) -> NativeResult<()> {
    if request_id.trim().is_empty()
        || request_id.len() > 96
        || request_id.chars().any(char::is_control)
    {
        return Err(invalid("Manim production request id is invalid"));
    }
    Ok(())
}

fn verified_manim_camera_getter_digest(
    artifact: &Value,
    required: bool,
) -> NativeResult<Option<String>> {
    if !required {
        return Ok(None);
    }
    let digest = required_string(
        artifact,
        "/camera_getters_sha256",
        "Manim camera render has no verified native getter digest",
    )?;
    if !is_sha256(&digest) {
        return Err(backend("Manim camera getter digest is malformed"));
    }
    Ok(Some(digest))
}

#[derive(Clone)]
pub struct ProductionCoordinator {
    service: StudioService,
    client: ProductionClient,
    receipt_custody: receipt_custody::ReceiptCustody,
    #[cfg(test)]
    before_dispatch_admission: Option<std::sync::Arc<dyn Fn() + Send + Sync>>,
}

impl ProductionCoordinator {
    pub fn new(service: StudioService, connection: ProductionConnection) -> NativeResult<Self> {
        Ok(Self {
            service,
            client: ProductionClient::new(connection)?,
            receipt_custody: receipt_custody::ReceiptCustody::default(),
            #[cfg(test)]
            before_dispatch_admission: None,
        })
    }

    /// Private, non-durable custody of a terminal whose local append was not
    /// confirmed. This can contain native payloads and storage paths; place it
    /// only in the producer's existing private evidence, never public output.
    /// Inspection neither acknowledges an effect nor permits a redispatch.
    pub fn private_receipt_custody_snapshot(&self) -> Value {
        self.receipt_custody.snapshot()
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
        let has_saved_mix = project
            .production_design
            .delivery_recipes
            .get(&deliverable_id)
            .and_then(|recipe| recipe.original_mix.as_ref())
            .is_some();
        if !project.audio.sfx_cues.is_empty()
            || project.audio.mix.target_lufs.is_some()
            || project.audio.mix.target_true_peak_dbfs.is_some()
            || (!has_saved_mix && project.audio.mix.voice_gain_db != 0.0)
        {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Native master cannot omit authored gain/SFX or claim unmeasured loudness targets",
            ));
        }
        if has_saved_mix {
            let mix = crate::original_mix::prepare_saved_original_mix(
                &self.service,
                project_id,
                expected,
                deliverable_id,
            )
            .map_err(|error| Error::new(ErrorCode::Unsupported, error.to_string()))?;
            let audio_clock =
                u128::from(mix.plan().sample_frames) * u128::from(motion.frame_rate.num);
            let video_clock =
                u128::from(segment.frame_count) * u128::from(motion.frame_rate.den) * 48_000;
            if mix.wav_sha256() != audio.sha256
                || audio_clock.abs_diff(video_clock) > u128::from(motion.frame_rate.num)
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Staged soundtrack differs from the saved source mix or exact visual cut",
                ));
            }
        }

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
        validate_manim_request_id(request_id)?;
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

        self.realize_prepared_manim_scene(
            project_id,
            expected,
            request_id,
            scene_id,
            PreparedManimRender {
                start_command: "driver.manim-community.render.start",
                plan_value,
                plan_sha256,
                profile,
                requires_camera_getters: false,
            },
        )
        .await
    }

    /// Render the admitted camera profile from the current persisted project.
    /// Source preparation confers no execution grant; the existing Broker
    /// connection and receipt lifecycle authorize and retain the native job.
    pub async fn realize_manim_camera_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
        deliverable_id: Uuid,
    ) -> NativeResult<Value> {
        validate_manim_request_id(request_id)?;
        let compiled =
            crate::manim_camera_projection::prepare_saved_manim_camera_program_source_only(
                &self.service,
                project_id,
                expected,
                scene_id,
                deliverable_id,
            )?;
        let plan_value = serde_json::to_value(&compiled.prepared.source)
            .map_err(|_| invalid("Manim camera scene plan could not be encoded"))?;
        self.realize_prepared_manim_scene(
            project_id,
            expected,
            request_id,
            scene_id,
            PreparedManimRender {
                start_command: "driver.manim-community.camera.render.start",
                plan_value,
                plan_sha256: compiled.trusted_compiler.plan_sha256,
                profile: compiled.prepared.source.render_profile,
                requires_camera_getters: true,
            },
        )
        .await
    }

    async fn realize_prepared_manim_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
        prepared: PreparedManimRender,
    ) -> NativeResult<Value> {
        let PreparedManimRender {
            start_command,
            plan_value,
            plan_sha256,
            profile,
            requires_camera_getters,
        } = prepared;

        let started = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:start"),
                start_command,
                json!({"plan": plan_value, "profile": profile}),
                true,
            )
            .await?;
        let start_data = response_data(&started)
            .map_err(|error| unconfirmed_started_manim_render(error, start_command))?;
        let job_ref = required_string(
            start_data,
            "/job_ref",
            "Manim render did not return a job reference",
        )
        .map_err(|error| unconfirmed_started_manim_render(error, start_command))?;
        let provider_generation = started
            .pointer("/result/execution/provenance/provider_generation")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                unconfirmed_started_manim_render(
                    backend("Manim start did not retain its provider generation"),
                    start_command,
                )
            })?;

        let deadline = Instant::now() + Duration::from_secs(MANIM_RENDER_POLL_DEADLINE_SECS);
        let mut poll = 0_u32;
        loop {
            if Instant::now() >= deadline {
                return Err(unconfirmed_started_manim_render(
                    Error::new(
                        ErrorCode::Timeout,
                        "Manim render did not reach a terminal state before the bounded deadline",
                    ),
                    start_command,
                ));
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
                Err(error) => {
                    return Err(unconfirmed_started_manim_render(error, start_command));
                }
            };
            let status_data = require_acknowledged_manim_observation(
                &status,
                &job_ref,
                provider_generation,
                start_command,
                "status",
            )?;
            match status_data.get("state").and_then(Value::as_str) {
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
                    return Err(unconfirmed_started_manim_render(
                        backend("Manim Community returned an unsupported job state"),
                        start_command,
                    ));
                }
                None => {
                    return Err(unconfirmed_started_manim_render(
                        backend("Manim Community render status returned no state"),
                        start_command,
                    ));
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
            .await
            .map_err(|error| unconfirmed_started_manim_render(error, start_command))?;
        let result_data = require_acknowledged_manim_observation(
            &result,
            &job_ref,
            provider_generation,
            start_command,
            "result",
        )?;
        if result_data.get("state").and_then(Value::as_str) != Some("succeeded") {
            return Err(unconfirmed_started_manim_render(
                backend("Manim render result is not a successful terminal artifact"),
                start_command,
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
        let camera_getters_sha256 =
            verified_manim_camera_getter_digest(&artifact, requires_camera_getters)?;
        let verified_path = verify_output_artifact(
            &self.client.connection().output_root,
            &relative_path,
            &sha256,
            MANIM_VIDEO_MAX_BYTES,
        )?;

        let mut verified_artifact = json!({
            "relative_path": relative_path,
            "sha256": sha256,
            "bytes": bytes,
            "verified_path": verified_path
        });
        if let Some(digest) = camera_getters_sha256 {
            verified_artifact["camera_getters_sha256"] = json!(digest);
        }
        if requires_camera_getters {
            let source_check = serde_json::from_value(plan_value.clone())
                .map_err(|_| invalid("Original Manim camera plan cannot be reopened"))
                .and_then(
                    |plan: motionwright_manim_profile::planar_camera::ManimCameraScenePlanV1| {
                        crate::manim_camera_projection::verify_rendered_manim_camera_source(
                            &self.client.connection().output_root,
                            &relative_path,
                            &plan,
                            &artifact,
                        )
                    },
                );
            match source_check {
                Ok(source) => verified_artifact["camera_source"] = source,
                Err(error) => {
                    verified_artifact["camera_source_custody_error"] =
                        json!({"message": error.to_string()});
                }
            }
        }
        Ok(json!({
            "renderer": "manim-community",
            "native_driver": "driver:manim-community",
            "runtime_contract": "Manim Community 0.21.0",
            "project_revision": expected.revision,
            "scene_id": scene_id,
            "job_ref": job_ref,
            "plan_sha256": plan_sha256,
            "profile": profile,
            "result": result,
            "artifact": verified_artifact
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
        let mutation = mutation || WORKFLOW_MUTATING_COMMANDS.contains(&command);
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
            let unfinished_mutation = prior.stage == "dispatching"
                && prior.payload.get("mutation").and_then(Value::as_bool) == Some(true);
            return Err(if prior.stage == "outcome_unknown" || unfinished_mutation {
                error.uncertain()
            } else {
                error
            });
        }

        #[cfg(test)]
        if let Some(before_dispatch_admission) = &self.before_dispatch_admission {
            before_dispatch_admission();
        }

        // Capacity is claimed before the durable dispatch intent and native
        // effect. Every pre-terminal abort releases this reservation by Drop.
        let mut custody =
            self.receipt_custody
                .reserve(mutation, request_id, command, job_ref.as_deref())?;

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
                // Retain the exact Broker result and identifier before the
                // fallible append. The attempted stage is not a durable receipt.
                custody.observe(ProductionReceiptInput {
                    project_id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: request_id.into(),
                    request_sha256: digest,
                    command: command.into(),
                    stage: "completed".into(),
                    payload: json!({
                        "result": result.value,
                        "broker_request_id": result.request_id,
                        "job_ref": job_ref
                    }),
                });
                match self
                    .service
                    .append_production_receipt(custody.attempted_receipt())
                {
                    Ok(receipt) => {
                        custody.confirmed();
                        Ok(json!({
                            "replayed": false,
                            "result": receipt.payload["result"],
                            "receipt": receipt
                        }))
                    }
                    Err(storage_error) => {
                        custody.failed(storage_error);
                        let error = backend(
                            "Production completed but its local receipt could not be persisted",
                        );
                        Err(if mutation { error.uncertain() } else { error })
                    }
                }
            }
            Err(error) => {
                let stage = if error.outcome_known {
                    "failed_known"
                } else {
                    "outcome_unknown"
                };
                custody.observe(ProductionReceiptInput {
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
                match self
                    .service
                    .append_production_receipt(custody.attempted_receipt())
                {
                    Ok(_) => custody.confirmed(),
                    Err(storage_error) => custody.failed(storage_error),
                }
                // An append failure must not replace the original typed
                // native error or change its certainty.
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
    #[tokio::test]
    async fn stale_dispatch_after_peer_commit_never_enters_the_native_client() {
        use std::sync::{
            Arc, Mutex,
            atomic::{AtomicUsize, Ordering},
        };

        let (service, project, temp) = fixture_service();
        let database = temp.path().join("motionwright.sqlite3");
        let connection = fake_render_connection(&temp, project.resource_key());
        let mut coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();

        let execute_calls = Arc::clone(&coordinator.client.execute_calls);
        let hook_calls = Arc::new(AtomicUsize::new(0));
        let peer_result: Arc<Mutex<Option<motionwright_domain::Project>>> =
            Arc::new(Mutex::new(None));

        let project_id = project.id;
        let prepared_stamp = motionwright_domain::RevisionStamp::from(&project);
        let peer_stamp = motionwright_domain::RevisionStamp::from(&project);
        let peer_database = database.clone();
        let peer_result_for_hook = Arc::clone(&peer_result);
        let hook_calls_for_hook = Arc::clone(&hook_calls);

        coordinator.before_dispatch_admission = Some(Arc::new(move || {
            assert_eq!(
                hook_calls_for_hook.fetch_add(1, Ordering::SeqCst),
                0,
                "the peer edit must occur exactly once after the original preflight"
            );

            let peer = StudioService::open(&peer_database).unwrap();
            let edited = peer
                .apply(
                    project_id,
                    &peer_stamp,
                    "peer-edit-before-native-admission",
                    &motionwright_domain::Change::RenameProject {
                        title: "Peer committed before native dispatch".into(),
                    },
                )
                .unwrap()
                .project;

            *peer_result_for_hook.lock().unwrap() = Some(edited);
        }));

        let command = "driver.motion-canvas.render.start";
        let rejected_request = "native-stale-head-admission";
        let accepted_request = "native-current-head-admission";
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

        let error = coordinator
            .execute(
                project.id,
                &prepared_stamp,
                rejected_request,
                command,
                args.clone(),
                true,
            )
            .await
            .unwrap_err();

        assert_eq!(error.code, ErrorCode::StaleReference);
        assert!(error.outcome_known);
        assert_eq!(hook_calls.load(Ordering::SeqCst), 1);
        assert_eq!(
            execute_calls.load(Ordering::SeqCst),
            0,
            "failed dispatch admission must stop before ProductionClient::execute"
        );
        assert!(coordinator.receipts(project.id, 20).unwrap().is_empty());
        assert!(
            service
                .latest_production_receipt(project.id, rejected_request)
                .unwrap()
                .is_none()
        );

        let edited = peer_result.lock().unwrap().clone().unwrap();
        assert_eq!(edited.id, project.id);
        assert_eq!(edited.generation, project.generation);
        assert_eq!(edited.revision, project.revision + 1);
        assert_eq!(edited.title, "Peer committed before native dispatch");

        let independent = StudioService::open(&database).unwrap();
        assert_eq!(independent.project(project.id).unwrap(), edited);
        assert!(
            independent
                .production_receipts(project.id, 20)
                .unwrap()
                .is_empty()
        );
        drop(independent);

        // A fresh explicit request on the committed revision must still reach
        // the same concrete client. This excludes a permanently blocked fixture.
        coordinator.before_dispatch_admission = None;
        let current_stamp = motionwright_domain::RevisionStamp::from(&edited);
        let accepted = coordinator
            .execute(
                project.id,
                &current_stamp,
                accepted_request,
                command,
                args.clone(),
                true,
            )
            .await
            .unwrap();

        assert_eq!(accepted["replayed"], false);
        assert_eq!(accepted["result"]["data"]["job_ref"], "job-native-1");
        assert_eq!(execute_calls.load(Ordering::SeqCst), 1);
        assert_eq!(hook_calls.load(Ordering::SeqCst), 1);

        let original_receipts = coordinator.receipts(project.id, 20).unwrap();
        assert_eq!(original_receipts.len(), 2);
        assert!(original_receipts.iter().all(|receipt| {
            receipt.project_id == project.id
                && receipt.generation == edited.generation
                && receipt.revision == edited.revision
                && receipt.request_id == accepted_request
                && receipt.command == command
        }));

        let dispatch = original_receipts
            .iter()
            .find(|receipt| receipt.stage == "dispatching")
            .unwrap();
        let completed = original_receipts
            .iter()
            .find(|receipt| receipt.stage == "completed")
            .unwrap();
        assert_ne!(dispatch.id, completed.id);
        assert_eq!(dispatch.request_sha256, completed.request_sha256);

        // Reentry of this known completed request must return its original
        // result and receipt without another client invocation.
        let replayed = coordinator
            .execute(
                project.id,
                &current_stamp,
                accepted_request,
                command,
                args,
                true,
            )
            .await
            .unwrap();

        assert_eq!(replayed["replayed"], true);
        assert_eq!(replayed["result"], accepted["result"]);
        assert_eq!(replayed["receipt"], accepted["receipt"]);
        assert_eq!(execute_calls.load(Ordering::SeqCst), 1);
        assert_eq!(
            coordinator.receipts(project.id, 20).unwrap(),
            original_receipts
        );
        assert!(
            service
                .latest_production_receipt(project.id, rejected_request)
                .unwrap()
                .is_none()
        );

        drop(coordinator);
        drop(service);

        let reopened = StudioService::open(&database).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), edited);
        assert_eq!(
            reopened.production_receipts(project.id, 20).unwrap(),
            original_receipts
        );
        assert!(
            reopened
                .latest_production_receipt(project.id, rejected_request)
                .unwrap()
                .is_none()
        );
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

    #[test]
    fn camera_artifact_rejects_missing_and_malformed_getter_digest() {
        for artifact in [
            json!({}),
            json!({"camera_getters_sha256": null}),
            json!({"camera_getters_sha256": 42}),
            json!({"camera_getters_sha256": ""}),
            json!({"camera_getters_sha256": "a".repeat(63)}),
            json!({"camera_getters_sha256": "a".repeat(65)}),
            json!({"camera_getters_sha256": "AB".repeat(32)}),
            json!({"camera_getters_sha256": "g".repeat(64)}),
        ] {
            let error = verified_manim_camera_getter_digest(&artifact, true).unwrap_err();
            assert_eq!(error.code, ErrorCode::BackendFailed);
        }
    }

    #[test]
    fn camera_artifact_preserves_exact_native_getter_digest() {
        let digest = "ab".repeat(32);
        let artifact = json!({"camera_getters_sha256": digest});
        assert_eq!(
            verified_manim_camera_getter_digest(&artifact, true).unwrap(),
            Some(digest)
        );
    }

    #[test]
    fn legacy_manim_artifact_does_not_acquire_camera_requirements() {
        for artifact in [
            json!({"relative_path": "media/static.mp4"}),
            json!({"camera_getters_sha256": "legacy-extra-field"}),
        ] {
            assert_eq!(
                verified_manim_camera_getter_digest(&artifact, false).unwrap(),
                None
            );
        }
    }
    #[cfg(unix)]
    async fn assert_reopened_prior_mutation_outcome(stage: &str, expected_known: bool) {
        let (service, project, temp) = fixture_service();
        let command = "driver.motion-canvas.render.start";
        let request_id = "reopened-original-dispatch";
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
        let original_claim = ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: request_id.into(),
            request_sha256: request_digest(command, &args).unwrap(),
            command: command.into(),
            stage: "dispatching".into(),
            payload: json!({"mutation": true}),
        };
        service
            .append_production_receipt(original_claim.clone())
            .unwrap();
        if stage != "dispatching" {
            service
                .append_production_receipt(ProductionReceiptInput {
                    stage: stage.into(),
                    payload: json!({
                        "mutation": true,
                        "error": {
                            "code": "BackendFailed",
                            "outcome_known": stage == "failed_known"
                        }
                    }),
                    ..original_claim
                })
                .unwrap();
        }
        let before = service.production_receipts(project.id, 10).unwrap();
        drop(service);

        // This is a local coordinator recovery fixture. It does not configure
        // a real Broker, assert user authority or execute a native mutation.
        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            before
        );
        let mut connection = fake_connection(&temp, project.resource_key());
        let sentinel = temp.path().join("unexpected-native-dispatch");
        fs::write(
            &connection.executable,
            format!(
                "#!/bin/sh\nprintf dispatched > '{}'\nexit 97\n",
                sentinel.to_string_lossy().replace('\'', "'\\''")
            ),
        )
        .unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
        let coordinator = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args,
                true,
            )
            .await
            .unwrap_err();

        assert_eq!(error.code, ErrorCode::Conflict);
        assert_eq!(
            error.outcome_known, expected_known,
            "the persisted {stage} receipt determines original outcome knowledge"
        );
        assert!(
            !sentinel.exists(),
            "recovery dispatched a second native request"
        );
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            before
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
        drop(coordinator);
        drop(reopened);
        assert_eq!(
            StudioService::open(temp.path().join("motionwright.sqlite3"))
                .unwrap()
                .production_receipts(project.id, 10)
                .unwrap(),
            before
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn reopened_dispatching_mutation_preserves_unknown_without_redispatch() {
        assert_reopened_prior_mutation_outcome("dispatching", false).await;
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn reopened_outcome_unknown_remains_uncertain_without_redispatch() {
        assert_reopened_prior_mutation_outcome("outcome_unknown", false).await;
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn reopened_failed_known_preserves_known_failure_without_redispatch() {
        assert_reopened_prior_mutation_outcome("failed_known", true).await;
    }
    #[test]
    fn manim_observation_error_preserves_diagnostic_and_original_start() {
        for start_command in [
            "driver.manim-community.render.start",
            "driver.manim-community.camera.render.start",
        ] {
            let original = Error::new(
                ErrorCode::Unavailable,
                "original Manim provider generation diagnostic",
            );
            let expected = serde_json::to_value(
                original
                    .clone()
                    .uncertain()
                    .recipe_progress(1, start_command),
            )
            .unwrap();
            let retained = unconfirmed_started_manim_render(original, start_command);
            assert_eq!(serde_json::to_value(retained).unwrap(), expected);
        }
    }

    #[test]
    fn manim_observations_require_original_job_and_provider_generation() {
        let command = "driver.manim-community.camera.render.start";
        let job = "private-native-original";
        let correct = json!({
            "result": {
                "data": {"job_ref": job, "state": "succeeded"},
                "execution": {"provenance": {"provider_generation": 17}}
            }
        });
        for kind in ["status", "result"] {
            assert!(
                require_acknowledged_manim_observation(&correct, job, 17, command, kind).is_ok()
            );
            for (pointer, replacement) in [
                ("/result/data/job_ref", json!("private-native-foreign")),
                ("/result/data/job_ref", serde_json::Value::Null),
                (
                    "/result/execution/provenance/provider_generation",
                    json!(18),
                ),
                (
                    "/result/execution/provenance/provider_generation",
                    serde_json::Value::Null,
                ),
            ] {
                let mut wrong = correct.clone();
                *wrong.pointer_mut(pointer).unwrap() = replacement;
                let error = require_acknowledged_manim_observation(&wrong, job, 17, command, kind)
                    .unwrap_err();
                assert_eq!(error.code, ErrorCode::BackendFailed);
                assert!(!error.outcome_known);
                assert!(!error.message.contains("private-native-"));
            }
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn manim_foreign_or_failed_observations_cannot_complete_or_redispatch() {
        // Synthetic Broker envelopes exercise the coordinator boundary only.
        // They do not execute Manim, prove pixels, or claim native acceptance.
        for start_command in [
            "driver.manim-community.render.start",
            "driver.manim-community.camera.render.start",
        ] {
            for case in [
                "foreign_status",
                "missing_status",
                "generation_status",
                "error_status",
                "foreign_result",
                "missing_result",
                "generation_result",
                "error_result",
            ] {
                let (service, project, temp) = fixture_service();
                let mut connection = fake_render_connection(&temp, project.resource_key());
                let script = fs::read_to_string(&connection.executable)
                    .unwrap()
                    .replace("driver.motion-canvas", "driver.manim-community")
                    .replace("driver:motion-canvas", "driver:manim-community")
                    .replace(
                        "driver.manim-community.render.start)",
                        "driver.manim-community.render.start|driver.manim-community.camera.render.start)",
                    );
                let status_line = r#"data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60}}'"#;
                let result_line = r#"data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60,"manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'"#;
                let is_result = case.ends_with("_result");
                let line = if is_result { result_line } else { status_line };
                assert_eq!(script.matches(line).count(), 1, "fixture source drift");
                let replacement = if case.starts_with("foreign_") {
                    line.replacen("job-native-1", "private-native-foreign", 1)
                } else if case.starts_with("missing_") {
                    line.replacen(r#""job_ref":"job-native-1","#, "", 1)
                } else {
                    let is_error = case.starts_with("error_");
                    let envelope = Envelope {
                        ok: !is_error,
                        request_id: "original-observation".into(),
                        command: if is_result {
                            "driver.manim-community.render.result"
                        } else {
                            "driver.manim-community.render.status"
                        }
                        .into(),
                        data: (!is_error)
                            .then(|| json!({"job_ref":"job-native-1","state":"succeeded"})),
                        error: is_error.then(|| {
                            Error::new(
                                ErrorCode::Unavailable,
                                "original Manim observation diagnostic",
                            )
                        }),
                        execution: Execution {
                            backend: "driver:manim-community".into(),
                            duration_ms: 1,
                            policy_decision: "allow".into(),
                            fallbacks_attempted: vec![],
                            provenance: Some(InvocationProvenance {
                                provider: "driver:manim-community".into(),
                                source: SourceKind::Driver,
                                provider_version: "test".into(),
                                capability_version: "test".into(),
                                descriptor_sha256: "cd".repeat(32),
                                untrusted_metadata: false,
                                catalog_revision: 1,
                                execution_provider: Some("driver:manim-community".into()),
                                provider_generation: Some(if is_error { 2 } else { 3 }),
                            }),
                            dry_run: false,
                        },
                        warnings: vec![],
                    };
                    format!(
                        "printf '%s\\n' '{}'\n    exit 0",
                        serde_json::to_string(&envelope)
                            .unwrap()
                            .replace('\'', "'\\''")
                    )
                };
                let altered = script.replace(line, &replacement);
                assert_ne!(script, altered, "fixture must exercise {case}");
                fs::write(&connection.executable, altered).unwrap();
                connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
                let coordinator = ProductionCoordinator::new(service, connection).unwrap();
                for attempt in 0..2 {
                    let error = coordinator
                        .realize_prepared_manim_scene(
                            project.id,
                            &project.stamp(),
                            "original-manim-attempt",
                            Uuid::nil(),
                            PreparedManimRender {
                                start_command,
                                plan_value: json!({"coordinator_fixture_only": true}),
                                plan_sha256: "ab".repeat(32),
                                profile: ManimRenderProfile {
                                    width: 640,
                                    height: 360,
                                    frame_rate: 30,
                                },
                                requires_camera_getters: start_command.contains(".camera."),
                            },
                        )
                        .await
                        .expect_err("unbound observation may not produce verified native media");
                    assert!(
                        !error.outcome_known,
                        "{start_command} {case} attempt {attempt}"
                    );
                    if attempt == 0 && case.starts_with("error_") {
                        assert_eq!(error.code, ErrorCode::Unavailable);
                        assert_eq!(error.message, "original Manim observation diagnostic");
                    }
                }
                let receipts = coordinator.receipts(project.id, 100).unwrap();
                assert_eq!(
                    receipts
                        .iter()
                        .filter(|receipt| {
                            receipt.command == start_command && receipt.stage == "dispatching"
                        })
                        .count(),
                    1,
                    "same original request cannot dispatch another native effect"
                );
                assert_eq!(
                    receipts
                        .iter()
                        .filter(|receipt| {
                            receipt.command == start_command && receipt.stage == "completed"
                        })
                        .count(),
                    1
                );
            }
        }
    }

    #[cfg(unix)]
    fn transport_reply(command: &str) -> Envelope {
        let authority = expected_authority(command).unwrap();
        Envelope {
            ok: true,
            request_id: "transport-fixture-original".into(),
            command: command.into(),
            data: Some(json!({"changed": true})),
            error: None,
            execution: Execution {
                backend: authority.provider.into(),
                duration_ms: 1,
                policy_decision: "allow".into(),
                fallbacks_attempted: vec![],
                provenance: Some(InvocationProvenance {
                    provider: authority.provider.into(),
                    source: authority.source,
                    provider_version: "test".into(),
                    capability_version: "test".into(),
                    descriptor_sha256: "ab".repeat(32),
                    untrusted_metadata: false,
                    catalog_revision: 1,
                    execution_provider: Some(authority.provider.into()),
                    provider_generation: Some(17),
                }),
                dry_run: false,
            },
            warnings: vec![],
        }
    }

    #[cfg(unix)]
    fn fake_transport_connection(
        temp: &tempfile::TempDir,
        resource: String,
        stdout: &str,
        stderr: &str,
        exit_code: u8,
    ) -> (ProductionConnection, PathBuf) {
        let mut connection = fake_connection(temp, resource);
        let counter = temp.path().join("transport-invocations");
        fs::write(
            &connection.executable,
            format!(
                "#!/bin/sh\nprintf 'invoked\\n' >> '{}'\nprintf '%s' '{}'\nprintf '%s' '{}' >&2\nexit {exit_code}\n",
                counter.to_string_lossy().replace('\'', "'\\''"),
                stdout.replace('\'', "'\\''"),
                stderr.replace('\'', "'\\''"),
            ),
        )
        .unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
        (connection, counter)
    }

    #[cfg(unix)]
    async fn assert_transport_failure(
        stdout: &str,
        stderr: &str,
        exit_code: u8,
        command: &str,
        mutation: bool,
        expected_error: Error,
    ) {
        // A local subprocess exercises the real transport and durable receipt
        // boundary. No Broker, native application or human approval is involved.
        let (service, project, temp) = fixture_service();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), stdout, stderr, exit_code);
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let args = json!({"transport_fixture_only": true});
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "original-transport-attempt",
                command,
                args.clone(),
                mutation,
            )
            .await
            .unwrap_err();
        let expected_json = serde_json::to_value(&expected_error).unwrap();
        assert_eq!(serde_json::to_value(&error).unwrap(), expected_json);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        let receipts = service.production_receipts(project.id, 10).unwrap();
        assert_eq!(receipts.len(), 2);
        assert_eq!(
            receipts
                .iter()
                .filter(|receipt| receipt.stage == "dispatching")
                .count(),
            1
        );
        let terminal = service
            .latest_production_receipt(project.id, "original-transport-attempt")
            .unwrap()
            .unwrap();
        assert_eq!(
            terminal.stage,
            if expected_error.outcome_known {
                "failed_known"
            } else {
                "outcome_unknown"
            }
        );
        assert_eq!(terminal.payload["error"], expected_json);
        assert_eq!(terminal.payload["mutation"], json!(mutation));
        drop(coordinator);
        drop(service);

        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            receipts
        );
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let retry = recovered
            .execute(
                project.id,
                &project.stamp(),
                "original-transport-attempt",
                command,
                args,
                mutation,
            )
            .await
            .unwrap_err();
        assert_eq!(retry.code, ErrorCode::Conflict);
        assert_eq!(retry.outcome_known, expected_error.outcome_known);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            receipts
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_spawn_failure_is_known_without_an_invocation() {
        let (service, project, temp) = fixture_service();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), "", "", 0);
        fs::set_permissions(&connection.executable, fs::Permissions::from_mode(0o600)).unwrap();
        connection.validate().unwrap();
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "spawn-failed",
                "driver.blender.collection.create",
                json!({"name": "transport-fixture"}),
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unavailable);
        assert_eq!(error.message, "Semwright CLI could not be started");
        assert!(error.outcome_known);
        assert!(!counter.exists());
        let terminal = service
            .latest_production_receipt(project.id, "spawn-failed")
            .unwrap()
            .unwrap();
        assert_eq!(terminal.stage, "failed_known");
        assert_eq!(
            terminal.payload["error"],
            serde_json::to_value(error).unwrap()
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_argument_rejection_precedes_spawn() {
        let (service, project, temp) = fixture_service();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), "", "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "oversized-arguments",
                "driver.blender.collection.create",
                json!({"name": "x".repeat(MAX_ARGS_BYTES + 1)}),
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert_eq!(
            error.message,
            "Production command arguments exceed the transport budget"
        );
        assert!(error.outcome_known);
        assert!(!counter.exists());
        assert_eq!(
            service
                .latest_production_receipt(project.id, "oversized-arguments")
                .unwrap()
                .unwrap()
                .stage,
            "failed_known"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_typed_consent_and_unknown_preserve_original_errors() {
        let command = "driver.blender.collection.create";
        for original in [
            Error::new(ErrorCode::ConsentRequired, "original approval decision"),
            Error::new(ErrorCode::BackendFailed, "original native diagnostic")
                .uncertain()
                .recipe_progress(1, command),
        ] {
            let mut envelope = transport_reply(command);
            envelope.ok = false;
            envelope.data = None;
            envelope.error = Some(original.clone());
            assert_transport_failure(
                &serde_json::to_string(&envelope).unwrap(),
                "",
                19,
                command,
                true,
                original,
            )
            .await;
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_unbound_mutation_replies_remain_unknown_after_reopen() {
        let command = "driver.blender.collection.create";
        for stdout in ["", "{invalid"] {
            assert_transport_failure(
                stdout,
                "",
                0,
                command,
                true,
                backend("Semwright CLI returned an invalid response envelope").uncertain(),
            )
            .await;
        }
        let base = serde_json::to_value(transport_reply(command)).unwrap();
        for (pointer, replacement, message) in [
            (
                "/command",
                json!("driver.blender.material.create"),
                "Semwright CLI response command does not match the request",
            ),
            (
                "/execution/provenance",
                Value::Null,
                "Canonical production response has no provenance",
            ),
            (
                "/execution/provenance/provider",
                json!("driver:manim-community"),
                "Canonical response provenance does not match the expected authority",
            ),
            (
                "/execution/provenance/source",
                serde_json::to_value(SourceKind::Builtin).unwrap(),
                "Canonical response provenance does not match the expected authority",
            ),
            (
                "/execution/provenance/provider_generation",
                Value::Null,
                "Canonical response provenance does not match the expected authority",
            ),
            (
                "/execution/provenance/descriptor_sha256",
                json!("invalid-digest"),
                "Canonical response provenance does not match the expected authority",
            ),
            (
                "/data",
                Value::Null,
                "Canonical production response has no data",
            ),
        ] {
            let mut envelope = base.clone();
            *envelope.pointer_mut(pointer).unwrap() = replacement;
            assert_transport_failure(
                &serde_json::to_string(&envelope).unwrap(),
                "",
                0,
                command,
                true,
                backend(message).uncertain(),
            )
            .await;
        }
        for (ok, exit_code) in [(false, 0), (true, 23)] {
            let mut envelope = transport_reply(command);
            envelope.ok = ok;
            assert_transport_failure(
                &serde_json::to_string(&envelope).unwrap(),
                "",
                exit_code,
                command,
                true,
                backend("Canonical production command failed without a typed error").uncertain(),
            )
            .await;
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_response_budget_failure_preserves_unknown() {
        let command = "driver.blender.collection.create";
        let valid = serde_json::to_string(&transport_reply(command)).unwrap();
        let oversized = "x".repeat(MAX_OUTPUT_BYTES + 1);
        for (stdout, stderr) in [
            (oversized.as_str(), ""),
            (valid.as_str(), oversized.as_str()),
        ] {
            assert_transport_failure(
                stdout,
                stderr,
                0,
                command,
                true,
                Error::new(
                    ErrorCode::ResourceExhausted,
                    "Semwright CLI response exceeded the transport budget",
                )
                .uncertain(),
            )
            .await;
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_read_only_failure_remains_known() {
        assert_transport_failure(
            "{invalid",
            "",
            0,
            "driver.motion-canvas.composition.inspect",
            false,
            backend("Semwright CLI returned an invalid response envelope"),
        )
        .await;
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_success_replays_receipt_without_a_second_invocation() {
        let command = "driver.blender.collection.create";
        let (service, project, temp) = fixture_service();
        let output = serde_json::to_string(&transport_reply(command)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let args = json!({"transport_fixture_only": true});
        let first = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "acknowledged-original",
                command,
                args.clone(),
                true,
            )
            .await
            .unwrap();
        assert_eq!(first["replayed"], json!(false));
        assert_eq!(first["result"]["data"], json!({"changed": true}));
        assert_eq!(first["receipt"]["stage"], json!("completed"));
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        let receipts = service.production_receipts(project.id, 10).unwrap();
        drop(coordinator);
        drop(service);

        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let replay = recovered
            .execute(
                project.id,
                &project.stamp(),
                "acknowledged-original",
                command,
                args,
                true,
            )
            .await
            .unwrap();
        assert_eq!(replay["replayed"], json!(true));
        assert_eq!(replay["result"], first["result"]);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            receipts
        );
    }

    #[cfg(unix)]
    mod pipe_deadline {
        use super::*;
        use tokio::time::timeout;

        // Only this isolated fixture creates a descendant. It never contacts a
        // Broker/provider, and every descendant has a finite 75-second lifetime.
        // A private release file ends it earlier, including on test failure.
        struct PipeFixture {
            root: PathBuf,
        }

        impl PipeFixture {
            async fn marker(&self, name: &str) -> String {
                timeout(Duration::from_secs(5), async {
                    loop {
                        if let Ok(value) = fs::read_to_string(self.root.join(name))
                            && !value.is_empty()
                        {
                            return value;
                        }
                        sleep(Duration::from_millis(10)).await;
                    }
                })
                .await
                .unwrap_or_else(|_| panic!("isolated pipe fixture did not publish {name}"))
            }

            async fn ready(&self) {
                assert_eq!(self.marker("pipe-ready").await, "ready");
                assert_eq!(self.marker("parent-exit").await, "exit-zero");
            }

            async fn finish(&self, instruction: &str) -> String {
                fs::write(self.root.join("pipe-release"), instruction).unwrap();
                self.marker("pipe-finished").await
            }
        }

        impl Drop for PipeFixture {
            fn drop(&mut self) {
                if !self.root.join("pipe-release").exists() {
                    let _ = fs::write(self.root.join("pipe-release"), "stop");
                }
            }
        }

        fn connection(
            temp: &tempfile::TempDir,
            resource: String,
            stdout: &str,
            pipe: &str,
        ) -> (ProductionConnection, PipeFixture) {
            assert!(matches!(pipe, "stdout" | "stderr" | "none"));
            let mut connection = fake_connection(temp, resource);
            fs::write(
                temp.path().join("pipe-fixture.json"),
                serde_json::to_vec(&json!({"stdout": stdout, "pipe": pipe})).unwrap(),
            )
            .unwrap();
            fs::write(
                &connection.executable,
                r#"#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
config = json.loads((root / "pipe-fixture.json").read_text(encoding="utf-8"))
with (root / "transport-invocations").open("a", encoding="utf-8") as counter:
    counter.write("invoked\n")
if config["pipe"] != "none":
    descendant = os.fork()
    if descendant == 0:
        fd = 1 if config["pipe"] == "stdout" else 2
        os.close(3 - fd)
        (root / "pipe-ready").write_text("ready", encoding="utf-8")
        limit = time.monotonic() + 75
        release = root / "pipe-release"
        while not release.exists() and time.monotonic() < limit:
            time.sleep(0.01)
        result = "finite-lifetime-expired"
        if release.exists():
            instruction = release.read_text(encoding="utf-8")
            result = "released"
            if instruction == "observe-reader":
                try:
                    os.write(fd, b"pipe-cancellation-probe")
                except BrokenPipeError:
                    result = "reader-closed"
                else:
                    result = "reader-still-open"
        os.close(fd)
        (root / "pipe-finished").write_text(result, encoding="utf-8")
        os._exit(0)
sys.stdout.write(config["stdout"])
sys.stdout.flush()
(root / "parent-exit").write_text("exit-zero", encoding="utf-8")
os._exit(0)
"#,
            )
            .unwrap();
            connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
            (
                connection,
                PipeFixture {
                    root: temp.path().to_path_buf(),
                },
            )
        }

        async fn assert_execute_deadline(pipe: &str, mutation: bool) {
            let (service, project, temp) = fixture_service();
            let command = "driver.motion-canvas.composition.inspect";
            let stdout = serde_json::to_string(&transport_reply(command)).unwrap();
            let (connection, fixture) = connection(&temp, project.resource_key(), &stdout, pipe);
            let coordinator =
                ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
            let args = json!({"local_pipe_fixture_only": true});
            let started = Instant::now();
            let observed = timeout(
                Duration::from_secs(DEFAULT_DEADLINE_SECS + 3),
                coordinator.execute(
                    project.id,
                    &project.stamp(),
                    "original-pipe-attempt",
                    command,
                    args.clone(),
                    mutation,
                ),
            )
            .await;
            let elapsed = started.elapsed();
            fixture.ready().await;
            let cleanup = fixture.finish("stop").await;
            eprintln!(
                "pipe_deadline pipe={pipe} mutation={mutation} elapsed_ms={} cleanup={cleanup}",
                elapsed.as_millis()
            );
            assert_eq!(cleanup, "released");
            assert!(
                observed.is_ok(),
                "the real client exceeded its {DEFAULT_DEADLINE_SECS}s process/output deadline"
            );
            let error = observed.unwrap().unwrap_err();
            assert_eq!(error.code, ErrorCode::Timeout);
            assert_eq!(error.message, "Canonical production command timed out");
            assert_eq!(error.outcome_known, !mutation);
            assert_eq!(
                fs::read(temp.path().join("transport-invocations")).unwrap(),
                b"invoked\n"
            );
            let receipts = service.production_receipts(project.id, 10).unwrap();
            let terminal = service
                .latest_production_receipt(project.id, "original-pipe-attempt")
                .unwrap()
                .unwrap();
            assert_eq!(
                terminal.stage,
                if mutation {
                    "outcome_unknown"
                } else {
                    "failed_known"
                }
            );
            assert_eq!(
                terminal.payload["error"],
                serde_json::to_value(error).unwrap()
            );
            drop(coordinator);
            drop(service);

            let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
            assert_eq!(reopened.project(project.id).unwrap(), project);
            assert_eq!(
                reopened.production_receipts(project.id, 10).unwrap(),
                receipts
            );
            let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
            let recovered_error = recovered
                .execute(
                    project.id,
                    &project.stamp(),
                    "original-pipe-attempt",
                    command,
                    args,
                    mutation,
                )
                .await
                .unwrap_err();
            assert_eq!(recovered_error.code, ErrorCode::Conflict);
            assert_eq!(recovered_error.outcome_known, !mutation);
            assert_eq!(
                fs::read(temp.path().join("transport-invocations")).unwrap(),
                b"invoked\n",
                "recovery may not enter the fixture CLI again"
            );
            assert_eq!(
                reopened.production_receipts(project.id, 10).unwrap(),
                receipts
            );
        }

        async fn assert_probe_deadline(pipe: &str) {
            let (_service, project, temp) = fixture_service();
            let (connection, fixture) =
                connection(&temp, project.resource_key(), "semwright 1.0.0\n", pipe);
            let client = ProductionClient::new(connection).unwrap();
            let started = Instant::now();
            let observed = timeout(
                Duration::from_secs(RUNTIME_PROBE_DEADLINE_SECS + 3),
                client.probe_runtime("1.0.0"),
            )
            .await;
            fixture.ready().await;
            let cleanup = fixture.finish("stop").await;
            eprintln!(
                "probe_deadline pipe={pipe} elapsed_ms={} cleanup={cleanup}",
                started.elapsed().as_millis()
            );
            assert_eq!(cleanup, "released");
            assert!(
                observed.is_ok(),
                "version output escaped the real probe deadline"
            );
            let error = observed.unwrap().unwrap_err();
            assert_eq!(error.code, ErrorCode::Timeout);
            assert!(error.outcome_known);
            assert_eq!(error.message, "Semwright CLI version probe timed out");
        }

        async fn assert_cancel_closes_reader(pipe: &str) {
            let (_service, project, temp) = fixture_service();
            let command = "driver.motion-canvas.composition.inspect";
            let stdout = serde_json::to_string(&transport_reply(command)).unwrap();
            let (connection, fixture) = connection(&temp, project.resource_key(), &stdout, pipe);
            let client = ProductionClient::new(connection).unwrap();
            let mut pending = Box::pin(client.execute(command, json!({}), false));
            tokio::select! {
                _ = fixture.ready() => {},
                result = &mut pending => panic!("fixture pipe must still be held: {result:?}"),
            }
            drop(pending);
            let outcome = fixture.finish("observe-reader").await;
            eprintln!("cancelled_client pipe={pipe} descendant_observed={outcome}");
            assert_eq!(
                outcome, "reader-closed",
                "cancelled client left a detached pipe reader"
            );
        }

        #[tokio::test]
        async fn execute_stdout_retained_after_exit_obeys_deadline() {
            assert_execute_deadline("stdout", false).await;
        }

        #[tokio::test]
        async fn execute_stderr_timeout_remains_unknown_after_reopen() {
            // The read-only local fixture has no effect. This flag exercises
            // conservative classification for a caller-declared mutation only.
            assert_execute_deadline("stderr", true).await;
        }

        #[tokio::test]
        async fn runtime_probe_stdout_retained_after_exit_obeys_deadline() {
            assert_probe_deadline("stdout").await;
        }

        #[tokio::test]
        async fn runtime_probe_stderr_retained_after_exit_obeys_deadline() {
            assert_probe_deadline("stderr").await;
        }

        #[tokio::test]
        async fn cancellation_closes_descendant_stdout_reader() {
            assert_cancel_closes_reader("stdout").await;
        }

        #[tokio::test]
        async fn cancellation_closes_descendant_stderr_reader() {
            assert_cancel_closes_reader("stderr").await;
        }

        #[tokio::test]
        async fn same_fixture_without_retained_pipe_accepts_valid_response() {
            let (_service, project, temp) = fixture_service();
            let command = "driver.motion-canvas.composition.inspect";
            let stdout = serde_json::to_string(&transport_reply(command)).unwrap();
            let (connection, fixture) = connection(&temp, project.resource_key(), &stdout, "none");
            let client = ProductionClient::new(connection).unwrap();
            let result = timeout(
                Duration::from_secs(5),
                client.execute(command, json!({}), false),
            )
            .await
            .unwrap()
            .unwrap();
            assert_eq!(result.request_id, "transport-fixture-original");
            assert_eq!(result.value["provenance_checked"], true);
            assert_eq!(fixture.marker("parent-exit").await, "exit-zero");
            assert_eq!(
                fs::read(temp.path().join("transport-invocations")).unwrap(),
                b"invoked\n"
            );
        }
    }
    #[cfg(unix)]
    fn paused_custody_connection(
        temp: &tempfile::TempDir,
        resource: String,
        envelope: &Envelope,
    ) -> (ProductionConnection, PathBuf, PathBuf) {
        let (mut connection, counter) = fake_transport_connection(
            temp,
            resource,
            &serde_json::to_string(envelope).unwrap(),
            "",
            if envelope.ok { 0 } else { 19 },
        );
        let gate = temp.path().join("release-original-terminal");
        let script = fs::read_to_string(&connection.executable).unwrap();
        let marker = "\nprintf '%s' ";
        assert_eq!(script.matches(marker).count(), 2, "fixture source drift");
        let paused = script.replacen(
            marker,
            &format!(
                "\nwhile [ ! -f '{}' ]; do sleep 0.01; done\nprintf '%s' ",
                gate.to_string_lossy().replace('\'', "'\\''")
            ),
            1,
        );
        fs::write(&connection.executable, paused).unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
        (connection, counter, gate)
    }

    #[cfg(unix)]
    async fn custody_sqlite_append_failure(envelope: Envelope, mutation: bool) -> (Error, Value) {
        // This uses the existing CLI fixture and two real SQLite stores. It
        // does not run a Broker or a native application. The original intent
        // commits before the fixture is allowed to return its terminal.
        let (service, project, temp) = fixture_service();
        let command = envelope.command.clone();
        let (connection, counter, gate) =
            paused_custody_connection(&temp, project.resource_key(), &envelope);
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let clone = coordinator.clone();
        let args = json!({"job_ref": "private-exact-job", "custody_fixture_only": true});
        let expected = project.stamp();
        let native = coordinator.execute(
            project.id,
            &expected,
            "custody-append-attempt",
            &command,
            args.clone(),
            mutation,
        );
        let replace_store = async {
            tokio::time::timeout(Duration::from_secs(5), async {
                loop {
                    if fs::read(&counter).ok().as_deref() == Some(&b"invoked\n"[..]) {
                        break;
                    }
                    sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .expect("original CLI invocation did not reach the reply gate");
            let receipts = service.production_receipts(project.id, 10).unwrap();
            assert_eq!(receipts.len(), 1);
            assert_eq!(receipts[0].stage, "dispatching");
            assert_eq!(receipts[0].payload["mutation"], json!(mutation));

            // Replacing the same shared Store after dispatch gives the real
            // terminal append a NotFound error without mocking the append API.
            let replacement = Store::open(temp.path().join("empty-receipt-store.sqlite3")).unwrap();
            let shared = service.store();
            *shared.lock() = replacement;
            fs::write(&gate, b"allow-original-terminal\n").unwrap();
            receipts
        };
        let (terminal, original_receipts) = tokio::join!(native, replace_store);
        let error = terminal.expect_err("an unconfirmed local append must not become success");
        let snapshot = coordinator.private_receipt_custody_snapshot();
        assert_eq!(snapshot, clone.private_receipt_custody_snapshot());
        assert_eq!(snapshot["durable"], false);
        assert_eq!(snapshot["pending_without_terminal"], 0);
        assert_eq!(snapshot["entries"].as_array().unwrap().len(), 1);
        let entry = &snapshot["entries"][0];
        assert_eq!(entry["persistence"], "unconfirmed");
        assert_eq!(entry["mutation"], json!(mutation));
        assert_eq!(entry["attempted_receipt"]["project_id"], json!(project.id));
        assert_eq!(
            entry["attempted_receipt"]["generation"],
            json!(project.generation)
        );
        assert_eq!(entry["attempted_receipt"]["revision"], project.revision);
        assert_eq!(
            entry["attempted_receipt"]["request_id"],
            "custody-append-attempt"
        );
        assert_eq!(
            entry["attempted_receipt"]["request_sha256"],
            request_digest(&command, &args).unwrap()
        );
        assert_eq!(entry["attempted_receipt"]["command"], command);
        assert_eq!(
            entry["attempted_receipt"]["payload"]["job_ref"],
            "private-exact-job"
        );
        assert_eq!(entry["storage_error"]["original_retained_in_memory"], true);
        assert_eq!(
            entry["storage_error"]["display"]["text_prefix"],
            "project not found"
        );
        assert_eq!(entry["storage_error"]["debug"]["text_prefix"], "NotFound");
        assert_eq!(entry["storage_error"]["display"]["truncated"], false);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        drop(clone);
        drop(coordinator);
        drop(service);

        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            original_receipts
        );
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        // In-memory custody is deliberately not invented after a restart.
        assert_eq!(
            recovered.private_receipt_custody_snapshot()["entries"],
            json!([])
        );
        let retry = recovered
            .execute(
                project.id,
                &expected,
                "custody-append-attempt",
                &command,
                args,
                mutation,
            )
            .await
            .unwrap_err();
        assert_eq!(retry.code, ErrorCode::Conflict);
        assert_eq!(retry.outcome_known, !mutation);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            original_receipts
        );
        (error, snapshot)
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_custody_preserves_completed_terminal_on_sqlite_append_failure() {
        for (command, mutation) in [
            ("driver.blender.collection.create", true),
            ("driver.motion-canvas.composition.inspect", false),
        ] {
            let mut envelope = transport_reply(command);
            envelope.request_id = "private-original-broker-id".into();
            envelope.data =
                Some(json!({"native_original": "private-terminal-sentinel", "integer": 17}));
            let expected_result = json!({
                "data": envelope.data.clone().unwrap(),
                "execution": envelope.execution.clone(),
                "warnings": envelope.warnings.clone(),
                "provenance_checked": true,
            });
            let (error, snapshot) = custody_sqlite_append_failure(envelope, mutation).await;
            assert_eq!(error.code, ErrorCode::BackendFailed);
            assert_eq!(
                error.message,
                "Production completed but its local receipt could not be persisted"
            );
            assert_eq!(error.outcome_known, !mutation);
            assert!(!serde_json::to_string(&error).unwrap().contains("private-"));
            let attempt = &snapshot["entries"][0]["attempted_receipt"];
            assert_eq!(attempt["stage"], "completed");
            assert_eq!(attempt["payload"]["result"], expected_result);
            assert_eq!(
                attempt["payload"]["broker_request_id"],
                "private-original-broker-id"
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_custody_preserves_typed_error_when_its_append_also_fails() {
        let command = "driver.blender.collection.create";
        for original in [
            Error::new(ErrorCode::ConsentRequired, "private-original-consent"),
            Error::new(ErrorCode::BackendFailed, "private-original-native-error")
                .uncertain()
                .recipe_progress(1, command),
        ] {
            let mut envelope = transport_reply(command);
            envelope.ok = false;
            envelope.data = None;
            envelope.error = Some(original.clone());
            let (error, snapshot) = custody_sqlite_append_failure(envelope, true).await;
            assert_eq!(
                serde_json::to_value(&error).unwrap(),
                serde_json::to_value(&original).unwrap()
            );
            let attempt = &snapshot["entries"][0]["attempted_receipt"];
            assert_eq!(
                attempt["stage"],
                if original.outcome_known {
                    "failed_known"
                } else {
                    "outcome_unknown"
                }
            );
            assert_eq!(
                attempt["payload"]["error"],
                serde_json::to_value(original).unwrap()
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_custody_capacity_rejection_precedes_intent_and_invocation() {
        let (service, project, temp) = fixture_service();
        let command = "driver.blender.collection.create";
        let output = serde_json::to_string(&transport_reply(command)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let clone = coordinator.clone();
        let mut reservations = Vec::new();
        while let Ok(reservation) = clone
            .receipt_custody
            .reserve(true, "occupied", command, None)
        {
            reservations.push(reservation);
        }
        assert!(!reservations.is_empty());
        let state = clone.private_receipt_custody_snapshot();
        assert!(
            state["reserved_serialized_bytes"].as_u64().unwrap()
                <= state["serialized_byte_limit"].as_u64().unwrap()
        );
        // The independent byte limit becomes binding before the slot limit.
        assert!(reservations.len() < state["slot_limit"].as_u64().unwrap() as usize);
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "capacity-not-sent",
                command,
                json!({"name": "private-new-intent"}),
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert!(error.outcome_known);
        assert!(!counter.exists());
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
        drop(reservations);
        assert_eq!(
            coordinator.private_receipt_custody_snapshot()["reserved_slots"],
            0
        );
        assert_eq!(
            coordinator.private_receipt_custody_snapshot()["reserved_serialized_bytes"],
            0
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_custody_releases_on_pre_dispatch_storage_rejection() {
        let (service, project, temp) = fixture_service();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), "", "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "invalid-command-not-sent",
                "\n",
                json!({}),
                true,
            )
            .await
            .unwrap_err();
        assert!(error.outcome_known);
        assert!(!counter.exists());
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
        let snapshot = coordinator.private_receipt_custody_snapshot();
        assert_eq!(snapshot["reserved_slots"], 0);
        assert_eq!(snapshot["reserved_serialized_bytes"], 0);
        assert_eq!(snapshot["entries"], json!([]));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_custody_clears_only_after_confirmed_append_and_replay_is_effect_free() {
        let command = "driver.blender.collection.create";
        let (service, project, temp) = fixture_service();
        let output = serde_json::to_string(&transport_reply(command)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let args = json!({"name": "custody-confirmed"});
        let first = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "custody-confirmed",
                command,
                args.clone(),
                true,
            )
            .await
            .unwrap();
        assert_eq!(first["receipt"]["stage"], "completed");
        assert_eq!(first["replayed"], false);
        assert_eq!(
            coordinator.private_receipt_custody_snapshot()["reserved_slots"],
            0
        );
        assert_eq!(
            coordinator.private_receipt_custody_snapshot()["entries"],
            json!([])
        );
        let second = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "custody-confirmed",
                command,
                args,
                true,
            )
            .await
            .unwrap();
        assert_eq!(second["replayed"], true);
        assert_eq!(second["result"], first["result"]);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(
            service.production_receipts(project.id, 10).unwrap().len(),
            2
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_workflow_intent_matches_effective_client_mutation_after_reopen() {
        let command = "workflow.record.start";
        let (service, project, temp) = fixture_service();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), "{invalid", "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let args = json!({"synthetic_transport_only": true});
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "workflow-effective-mutation",
                command,
                args.clone(),
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::BackendFailed);
        assert_eq!(
            error.message,
            "Semwright CLI returned an invalid response envelope"
        );
        assert!(!error.outcome_known);
        let receipts = service.production_receipts(project.id, 10).unwrap();
        assert_eq!(receipts.len(), 2);
        let intent = receipts
            .iter()
            .find(|receipt| receipt.stage == "dispatching")
            .unwrap();
        assert_eq!(intent.payload["mutation"], true);
        let terminal = receipts
            .iter()
            .find(|receipt| receipt.stage == "outcome_unknown")
            .unwrap();
        assert_eq!(terminal.payload["mutation"], true);
        assert_eq!(
            terminal.payload["error"],
            serde_json::to_value(&error).unwrap()
        );
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let retry = recovered
            .execute(
                project.id,
                &project.stamp(),
                "workflow-effective-mutation",
                command,
                args,
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(retry.code, ErrorCode::Conflict);
        assert!(!retry.outcome_known);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            receipts
        );
    }
}
