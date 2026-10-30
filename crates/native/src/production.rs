use crate::{
    film::{FilmBuildOptions, plan_motion_canvas_segments},
    multi_renderer::{blender_export_path, build_blender_contribution, build_manim_plan},
};
use motionwright_domain::{
    AudioCodec, OutputColorSpace, OutputContainer, RevisionStamp, VideoCodec,
};
use motionwright_manim_profile::ManimRenderProfile;
use motionwright_service::{
    MAX_BRAND_FONT_BYTES, RootBudgetUnits, RootDispatchBudgetInput, RootDispatchBudgetOutcome,
    RootOperationBudget, RootOperationBudgetInput, StudioService,
};
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

mod audio_analysis;
mod audio_derivative;
mod blender_original;
mod blender_render;
mod brand_font_staging;
mod brand_fonts;
pub mod graph_authoring;
pub use brand_font_staging::{
    ProductionFontInvocationKind, ProductionFontPostcheck, ProductionFontStagingCustody,
    ProductionFontStagingOwner, ProductionFontStagingScope,
};
pub use graph_authoring::{
    NativeGraphRecipeClient, NativeGraphRecipeResult, NativeGraphSaveReceipt,
};
mod hybrid_budget;
mod hybrid_mlt;
mod hyperframes;
pub use hybrid_budget::HybridNativeSources;
pub use hybrid_mlt::{HybridNativeMaster, HybridNativeTimeline};
mod manim_graph;
mod manim_graph_profile;
pub use hyperframes::HyperframesSourceEvidence;
mod mlt_mezzanine;
mod mlt_multisegment_av;
mod mlt_timeline;
mod motion_canvas_window;
pub use motion_canvas_window::{
    MotionCanvasCachedWindowFrame, MotionCanvasCaptureTimings, MotionCanvasStartOrigin,
    MotionCanvasWindowCapture, MotionCanvasWindowCost, MotionCanvasWindowEvidence,
    MotionCanvasWindowRequest, MotionCanvasWindowSummary, MotionCanvasWindowUse,
    MotionCanvasWindowValidation, read_cached_motion_canvas_window_frame,
    reuse_motion_canvas_window,
};
mod native_runtime_versions;
#[cfg(test)]
mod path_boundary_tests;
mod provider_binding;
mod receipt_custody;
mod transport_custody;
pub use transport_custody::ProductionTransportCustody;
mod recovery;
mod renderer_registry;
mod renderer_versions;
mod voice_alignment;
pub use audio_analysis::{
    AudioAnalysisLane, AudioQcReceiptHint, AudioQcReceiptInventory, DeliveryAudioQcStatus,
    MeasuredMltAvMasterEvidence, MeasuredMltMultisegmentAvMasterEvidence, VerifiedAudioMeasurement,
    VerifiedDeliveryAudioQc, VerifiedDeliveryAudioQcReport, VerifiedOriginalMixAudioMeasurements,
};
pub use audio_derivative::{
    AdoptedAudioDerivative, AudioDerivativeFailure, AudioDerivativeIntent,
    AudioDerivativePcmObservation, AudioDerivativeProfile, ObservedAudioDerivative,
};
pub use blender_original::{
    BLENDER_PRODUCT_RECIPE_MEDIA_TYPE, BlenderOriginalEdit, BlenderOriginalFile,
    BlenderOriginalJournal, BlenderOriginalPhase, BlenderOriginalRequest,
    BlenderProductAnnotationEdit, BlenderProductAssetBinding, BlenderProductAttemptProjection,
    BlenderProductAttemptRequest, BlenderProductAttemptState, BlenderProductAttemptSummary,
    BlenderProductCycleJournal, BlenderProductCycleRequest, BlenderProductLineageSnapshot,
    BlenderProductNativeBinding, BlenderProductSourceScope, BlenderProductSourceSnapshot,
    decode_blender_product_recipe, read_blender_product_attempt, read_blender_product_source,
    realize_blender_product_cycle_with_connections,
};
pub use blender_render::{
    BLENDER_LIT_REQUIRED_ADDITIONS, BlenderLitArtifact, BlenderLitProjectProjection,
    BlenderLitSource, BlenderLitStillJournal, BlenderLitStillPhase, BlenderLitStillPlan,
    BlenderLitStillRequest, build_blender_lit_still_plan,
};
pub use manim_graph_profile::{ManimGraphProfileObservation, ManimGraphProfileState};
pub use mlt_mezzanine::{MltPreparedMezzanines, MltVerifiedMezzanine};
pub use mlt_multisegment_av::{MltMultisegmentAvMasterEvidence, MltMultisegmentAvMasterRequest};
pub use mlt_timeline::MltVerifiedLosslessTimeline;
pub use native_runtime_versions::{NativeRuntimeVersionObservation, NativeRuntimeVersionState};
pub use recovery::{CrossAppRecoveryReport, RecoveryBlenderProof, StageDisposition};
pub use renderer_registry::{RendererRegistryObservation, RendererRegistryState};
pub use renderer_versions::{ManimRuntimeVersionObservation, ManimRuntimeVersionState};
pub use voice_alignment::{QualifiedVoiceAlignment, VoiceAlignmentIntent};

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
// Match the independent frame byte ceiling used by native source-window
// verification. This is a maximal reserved output, not measured disk usage.
const MOTION_ROOT_FRAME_RESERVE_MIN_BYTES: u64 = 24 * 1024 * 1024;
// One full scene has at most two selected managed font imports, optional
// project creation, one apply and one render start. The remaining allowance
// covers bounded immutable plan/status/result/verify observations.
const MOTION_ROOT_NON_POLL_ATTEMPTS_PER_SEGMENT: u64 = 16;
const MLT_MEZZANINE_MAX_BYTES: u64 = 512 * 1024 * 1024;
const MLT_MASTER_MAX_BYTES: u64 = 1024 * 1024 * 1024;
const BLENDER_GLTF_MAX_BYTES: u64 = 256 * 1024 * 1024;
const MANIM_VIDEO_MAX_BYTES: u64 = 512 * 1024 * 1024;
const MANIM_RENDER_POLL_DEADLINE_SECS: u64 = 330;
const MANIM_RENDER_POLL_INTERVAL_MS: u64 = 1_000;
const MLT_SYNC_MIN_WINDOW_US: u64 = 10_000;
const MLT_SYNC_MAX_WINDOW_US: u64 = 500_000;

const HYPERFRAMES_COMMANDS: &[&str] = &[
    "driver.hyperframes.doctor",
    "driver.hyperframes.project.inspect",
    "driver.hyperframes.project.plan",
    "driver.hyperframes.project.apply",
    "driver.hyperframes.render.start",
    "driver.hyperframes.render.status",
    "driver.hyperframes.render.result",
];
const HYPERFRAMES_MUTATING_COMMANDS: &[&str] = &[
    "driver.hyperframes.project.apply",
    "driver.hyperframes.render.start",
];

const MOTION_CANVAS_COMMANDS: &[&str] = &[
    "driver.motion-canvas.doctor",
    "driver.motion-canvas.project.create",
    "driver.motion-canvas.project.inspect",
    "driver.motion-canvas.project.validate",
    "driver.motion-canvas.asset.import",
    "driver.motion-canvas.asset.list",
    "driver.motion-canvas.composition.inspect",
    "driver.motion-canvas.composition.plan",
    "driver.motion-canvas.composition.apply",
    "driver.motion-canvas.composition.verify",
    "driver.motion-canvas.render.start",
    "driver.motion-canvas.render.status",
    "driver.motion-canvas.render.cancel",
    "driver.motion-canvas.render.result",
];

const AUDIO_ANALYSIS_COMMANDS: &[&str] = &[
    "driver.audio-analysis.doctor",
    "driver.audio-analysis.artifact.measure",
];

const MLT_COMMANDS: &[&str] = &[
    "driver.mlt-video.doctor",
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
    "driver.blender.introspect.summary",
    "driver.blender.composition.measure",
    "driver.blender.animation.keyframe.insert",
    "driver.blender.scene.marker.add",
    "driver.blender.node.add",
    "driver.blender.node.remove",
    "driver.blender.node.link",
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
    "driver.blender.composition.inspect",
    "driver.blender.composition.plan",
    "driver.blender.composition.apply",
    "driver.blender.composition.verify",
    "driver.blender.render.settings",
    "driver.blender.render",
];

// These effectful Product operations retain UNKNOWN transport outcomes even
// when a caller supplies a read-only hint. SDK risk, permission and consent
// checks remain authoritative, including Destructive for node.remove.
const BLENDER_PRODUCT_MUTATING_COMMANDS: &[&str] = &[
    "driver.blender.composition.measure",
    "driver.blender.animation.keyframe.insert",
    "driver.blender.scene.marker.add",
    "driver.blender.node.add",
    "driver.blender.node.remove",
    "driver.blender.node.link",
];

const MANIM_COMMANDS: &[&str] = &[
    "driver.manim-community.doctor",
    "driver.manim-community.render.start",
    "driver.manim-community.static.render.start",
    "driver.manim-community.editable.render.start",
    "driver.manim-community.camera.render.start",
    "driver.manim-community.graph.render.start",
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
    output_artifact_path_with_minimum(root, relative, 1, max_bytes)
}

fn output_artifact_path_with_minimum(
    root: &Path,
    relative: &str,
    minimum_bytes: u64,
    max_bytes: u64,
) -> NativeResult<PathBuf> {
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
    if !metadata.is_file() || metadata.len() < minimum_bytes || metadata.len() > max_bytes {
        return Err(invalid("Production artifact is not a bounded regular file"));
    }
    Ok(candidate)
}

fn same_artifact_snapshot(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    if !left.is_file()
        || !right.is_file()
        || is_link_or_reparse(left)
        || is_link_or_reparse(right)
        || left.len() != right.len()
    {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        left.dev() == right.dev()
            && left.ino() == right.ino()
            && left.nlink() == right.nlink()
            && left.mode() == right.mode()
            && left.mtime() == right.mtime()
            && left.mtime_nsec() == right.mtime_nsec()
            && left.ctime() == right.ctime()
            && left.ctime_nsec() == right.ctime_nsec()
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        // These are the metadata available at this existing boundary, not a
        // claim of a Windows file-ID or retained handles for every ancestor.
        left.file_attributes() == right.file_attributes()
            && left.creation_time() == right.creation_time()
            && left.last_write_time() == right.last_write_time()
    }
    #[cfg(not(any(unix, windows)))]
    {
        false
    }
}

/// Private opening step for the common bounded production file capture. The flags
/// prevent a substituted leaf FIFO from waiting for a writer on Unix and avoid
/// following a substituted leaf link/reparse point. They do not isolate every
/// ancestor and do not bound ordinary filesystem wall time.
fn open_verified_artifact_input(
    path: &Path,
    before: &fs::Metadata,
) -> NativeResult<(fs::File, fs::Metadata)> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    #[cfg(not(any(unix, windows)))]
    return Err(Error::new(
        ErrorCode::Unsupported,
        "Verified native artifact opening is unavailable on this platform",
    ));
    let file = options.open(path).map_err(|_| {
        Error::new(
            ErrorCode::Unavailable,
            "Production artifact cannot be opened",
        )
    })?;
    let opened = file.metadata().map_err(|_| {
        Error::new(
            ErrorCode::Unavailable,
            "Production artifact handle metadata is unavailable",
        )
    })?;
    if !same_artifact_snapshot(before, &opened) {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Production artifact changed before its verified handle was opened",
        ));
    }
    Ok((file, opened))
}

/// Bytes and their descriptor observed by one bounded retained-file read.
///
/// This value does not verify an external pin or grant access to its source.
/// Callers must independently admit the root and retain any required owner
/// authority. It describes the captured bytes, not a future state of the path.
#[derive(Debug)]
pub struct CapturedProductionFile {
    bytes: Vec<u8>,
    descriptor: motionwright_storage::BlobDescriptor,
}

impl CapturedProductionFile {
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn descriptor(&self) -> &motionwright_storage::BlobDescriptor {
        &self.descriptor
    }

    pub fn into_parts(self) -> (Vec<u8>, motionwright_storage::BlobDescriptor) {
        (self.bytes, self.descriptor)
    }
}

/// Observe an existing regular file within a root already admitted by the caller.
///
/// The same retained descriptor, bounded read and before/after/path checks used
/// for verified production artifacts apply here. The returned SHA-256 and count
/// describe these bytes; they are not an expected pin, a grant, a continuous
/// ancestor-isolation claim or a filesystem wall-time bound. A zero-byte file
/// may be observed, but absence never becomes an empty capture.
pub fn capture_production_file(
    root: &Path,
    relative: &str,
    max_bytes: u64,
) -> NativeResult<CapturedProductionFile> {
    capture_production_file_with_bounds(root, relative, 0, None, max_bytes)
}

/// Read immutable production bytes only after the existing owner-root and grant
/// checks. The common bounded capture is followed by the exact expected SHA-256.
/// This wrapper preserves its nonempty artifact contract. It is not an
/// arbitrary-path desktop command or continuous ancestor-isolation claim.
pub fn read_verified_production_artifact(
    root: &Path,
    relative: &str,
    expected_sha256: &str,
    max_bytes: u64,
) -> NativeResult<Vec<u8>> {
    read_verified_production_bytes(root, relative, expected_sha256, 1, None, max_bytes)
}

// Original process streams may be empty, but must exist and match both their
// exact observed count and digest. The public artifact reader keeps minimum 1.
#[cfg(all(test, unix))]
fn read_verified_production_original(
    root: &Path,
    relative: &str,
    expected_sha256: &str,
    expected_bytes: u64,
    max_bytes: u64,
) -> NativeResult<Vec<u8>> {
    read_verified_production_bytes(
        root,
        relative,
        expected_sha256,
        0,
        Some(expected_bytes),
        max_bytes,
    )
}

fn read_verified_production_bytes(
    root: &Path,
    relative: &str,
    expected_sha256: &str,
    minimum_bytes: u64,
    expected_bytes: Option<u64>,
    max_bytes: u64,
) -> NativeResult<Vec<u8>> {
    // Validate external expectations before any source IO, as in the existing
    // verified artifact and exact-count original-custody wrappers.
    if expected_bytes.is_some_and(|bytes| bytes < minimum_bytes || bytes > max_bytes) {
        return Err(invalid(
            "Production original byte count exceeds its closed budget",
        ));
    }
    if !is_sha256(expected_sha256) {
        return Err(invalid("Production artifact digest is malformed"));
    }
    let captured = capture_production_file_with_bounds(
        root,
        relative,
        minimum_bytes,
        expected_bytes,
        max_bytes,
    )?;
    if captured.descriptor.sha256 != expected_sha256 {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Production artifact no longer matches its verified original",
        ));
    }
    Ok(captured.into_parts().0)
}

fn capture_production_file_with_bounds(
    root: &Path,
    relative: &str,
    minimum_bytes: u64,
    expected_bytes: Option<u64>,
    max_bytes: u64,
) -> NativeResult<CapturedProductionFile> {
    let read_limit = max_bytes
        .checked_add(1)
        .filter(|limit| max_bytes > 0 && *limit <= isize::MAX as u64)
        .ok_or_else(|| invalid("Production artifact read budget is invalid or overflows"))?;
    let path = output_artifact_path_with_minimum(root, relative, minimum_bytes, max_bytes)?;
    let before = safe_metadata(&path)?;
    if !before.is_file()
        || is_link_or_reparse(&before)
        || before.len() < minimum_bytes
        || before.len() > max_bytes
        || expected_bytes.is_some_and(|bytes| before.len() != bytes)
    {
        return Err(invalid("Production artifact is not a bounded regular file"));
    }
    let (mut file, opened) = open_verified_artifact_input(&path, &before)?;
    let capacity = usize::try_from(before.len())
        .map_err(|_| invalid("Production artifact byte count overflows memory addressing"))?;
    let mut bytes = Vec::new();
    bytes.try_reserve_exact(capacity).map_err(|_| {
        Error::new(
            ErrorCode::ResourceExhausted,
            "Production artifact read allocation failed",
        )
    })?;
    Read::by_ref(&mut file)
        .take(read_limit)
        .read_to_end(&mut bytes)
        .map_err(|_| Error::new(ErrorCode::Unavailable, "Production artifact cannot be read"))?;
    // A read failure returns above as the primary error. Successful reads must
    // still match the opened descriptor and current owner-root resolution.
    let after = file.metadata().map_err(|_| {
        Error::new(
            ErrorCode::Unavailable,
            "Production artifact handle cannot be rechecked",
        )
    })?;
    let resolved_after =
        output_artifact_path_with_minimum(root, relative, minimum_bytes, max_bytes)?;
    let current = safe_metadata(&resolved_after)?;
    if resolved_after != path
        || !same_artifact_snapshot(&before, &after)
        || !same_artifact_snapshot(&opened, &after)
        || !same_artifact_snapshot(&after, &current)
        || (bytes.len() as u64) < minimum_bytes
        || expected_bytes.is_some_and(|expected| bytes.len() as u64 != expected)
        || bytes.len() as u64 > max_bytes
        || bytes.len() as u64 != before.len()
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Production file changed during its bounded capture",
        ));
    }
    let descriptor = motionwright_storage::BlobDescriptor {
        sha256: hex::encode(Sha256::digest(&bytes)),
        size_bytes: bytes.len() as u64,
    };
    Ok(CapturedProductionFile { bytes, descriptor })
}

#[cfg(test)]
mod retained_file_capture_tests {
    use super::*;

    #[test]
    fn capture_observes_the_same_original_bytes_count_and_digest_without_an_external_pin() {
        let root = tempfile::tempdir().unwrap();
        let original = b"original bounded source bytes";
        fs::write(root.path().join("original.bin"), original).unwrap();
        let captured =
            capture_production_file(root.path(), "original.bin", original.len() as u64).unwrap();
        assert_eq!(captured.bytes(), original);
        assert_eq!(captured.descriptor().size_bytes, original.len() as u64);
        assert_eq!(
            captured.descriptor().sha256,
            hex::encode(Sha256::digest(original))
        );
        let (retained, descriptor) = captured.into_parts();
        assert_eq!(retained, original);
        assert_eq!(descriptor.size_bytes, retained.len() as u64);
        assert_eq!(descriptor.sha256, hex::encode(Sha256::digest(&retained)));
        assert!(
            read_verified_production_artifact(
                root.path(),
                "original.bin",
                &"00".repeat(32),
                original.len() as u64
            )
            .is_err()
        );
    }

    #[test]
    fn capture_of_an_empty_file_does_not_admit_a_missing_file_or_empty_verified_artifact() {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("empty.stdout"), b"").unwrap();
        let captured = capture_production_file(root.path(), "empty.stdout", 1).unwrap();
        let empty_sha = hex::encode(Sha256::digest(b""));
        assert!(captured.bytes().is_empty());
        assert_eq!(captured.descriptor().size_bytes, 0);
        assert_eq!(captured.descriptor().sha256, empty_sha);
        assert!(capture_production_file(root.path(), "missing.stdout", 1).is_err());
        assert!(
            read_verified_production_artifact(root.path(), "empty.stdout", &empty_sha, 1).is_err()
        );
        assert_eq!(
            read_verified_production_bytes(root.path(), "empty.stdout", &empty_sha, 0, Some(0), 1)
                .unwrap(),
            Vec::<u8>::new()
        );
        assert!(
            read_verified_production_bytes(root.path(), "empty.stdout", &empty_sha, 0, Some(1), 1)
                .is_err()
        );
    }

    #[test]
    fn capture_retains_its_bytes_when_the_source_path_changes_after_the_observation() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("original.bin");
        fs::write(&path, b"prior").unwrap();
        let captured = capture_production_file(root.path(), "original.bin", 5).unwrap();
        let observed_sha = captured.descriptor().sha256.clone();
        // Same length, different bytes. The old capture must not lazily resolve
        // its path, and its observed pin must reject the later source.
        fs::write(&path, b"later").unwrap();
        assert_eq!(captured.bytes(), b"prior");
        assert_eq!(captured.descriptor().size_bytes, 5);
        assert_eq!(
            captured.descriptor().sha256,
            hex::encode(Sha256::digest(b"prior"))
        );
        assert!(
            read_verified_production_artifact(root.path(), "original.bin", &observed_sha, 5)
                .is_err()
        );
        let later = capture_production_file(root.path(), "original.bin", 5).unwrap();
        assert_eq!(later.bytes(), b"later");
        assert_ne!(later.descriptor().sha256, captured.descriptor().sha256);
    }

    #[test]
    fn capture_keeps_closed_budgets_and_the_verified_wrappers_reject_invalid_pins_before_io() {
        let root = tempfile::tempdir().unwrap();
        for limit in [0, u64::MAX] {
            let error = capture_production_file(root.path(), "missing.bin", limit).unwrap_err();
            assert_eq!(error.code, ErrorCode::InvalidArgument);
        }
        let error =
            read_verified_production_artifact(root.path(), "missing.bin", "not-a-digest", 4)
                .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        let error = read_verified_production_bytes(
            root.path(),
            "missing.bin",
            &"00".repeat(32),
            0,
            Some(5),
            4,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        fs::write(root.path().join("original.bin"), b"1234").unwrap();
        assert!(capture_production_file(root.path(), "original.bin", 3).is_err());
        assert!(capture_production_file(root.path(), "../original.bin", 4).is_err());
        assert!(capture_production_file(root.path(), "nested/../original.bin", 4).is_err());
        let captured = capture_production_file(root.path(), "original.bin", 4).unwrap();
        assert_eq!(captured.descriptor().size_bytes, 4);
        assert!(
            read_verified_production_bytes(
                root.path(),
                "original.bin",
                &captured.descriptor().sha256,
                0,
                Some(3),
                4
            )
            .is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn initial_capture_uses_the_same_rejection_of_leaf_and_owner_directory_links() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("original.bin"), b"original").unwrap();
        symlink(
            root.path().join("original.bin"),
            root.path().join("alias.bin"),
        )
        .unwrap();
        assert!(capture_production_file(root.path(), "alias.bin", 8).is_err());
        fs::create_dir(root.path().join("owner")).unwrap();
        fs::write(root.path().join("owner/original.bin"), b"original").unwrap();
        fs::rename(
            root.path().join("owner"),
            root.path().join("retained-owner"),
        )
        .unwrap();
        symlink(
            root.path().join("retained-owner"),
            root.path().join("owner"),
        )
        .unwrap();
        assert!(capture_production_file(&root.path().join("owner"), "original.bin", 8).is_err());
    }
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
    if HYPERFRAMES_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:hyperframes",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if MOTION_CANVAS_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:motion-canvas",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if AUDIO_ANALYSIS_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:audio-analysis",
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
        || command == "doctor"
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
    transport_custody: Option<ProductionTransportCustody>,
    #[cfg(test)]
    execute_calls: std::sync::Arc<std::sync::atomic::AtomicUsize>,
}

impl ProductionClient {
    pub fn new(connection: ProductionConnection) -> NativeResult<Self> {
        connection.validate()?;
        Ok(Self {
            connection,
            transport_custody: None,
            #[cfg(test)]
            execute_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        })
    }

    pub fn connection(&self) -> &ProductionConnection {
        &self.connection
    }

    /// Opt in to bounded custody of this client's original transport bytes.
    /// This does not change provenance, receipts or dispatch permissions.
    pub fn with_transport_custody(mut self, custody: &ProductionTransportCustody) -> Self {
        self.transport_custody = Some(custody.clone());
        self
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
        self.execute_with_authority(command, args, mutation, None, None)
            .await
    }

    async fn execute_with_authority(
        &self,
        command: &str,
        args: Value,
        mutation: bool,
        authority: Option<ExpectedAuthority>,
        binding: Option<&provider_binding::BoundMediaContext>,
    ) -> NativeResult<BrokerResult> {
        #[cfg(test)]
        let _ = self
            .execute_calls
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);

        // The closed bound route keeps the complete parsed Core envelope.
        // Existing optional transport custody owns original binary stdout/stderr.
        let retain_original_core = authority.is_some();
        let mut capture: Option<transport_custody::CaptureReservation> = None;
        let mut recipe_terminal_error: Option<Error> = None;
        let result = async {
            let expected = authority
                .or_else(|| expected_authority(command))
                .ok_or_else(|| {
                    Error::new(ErrorCode::Unsupported, "Canonical command is not enabled")
                })?;
            // Only the backend-owned fixed Graph recipes select this private
            // route. The public command allowlist does not enable recipe.run.
            let recipe_stdin = command == graph_authoring::RECIPE_COMMAND
                && retain_original_core
                && expected.provider == "semwright-core"
                && expected.source == SourceKind::Builtin
                && binding.is_none();
            if command == graph_authoring::RECIPE_COMMAND && !recipe_stdin {
                return Err(Error::new(
                    ErrorCode::Unsupported,
                    "Graph recipe transport requires the fixed backend route",
                ));
            }
            let mutation = mutation
                || WORKFLOW_MUTATING_COMMANDS.contains(&command)
                || HYPERFRAMES_MUTATING_COMMANDS.contains(&command)
                || BLENDER_PRODUCT_MUTATING_COMMANDS.contains(&command)
                || command == manim_graph::START_COMMAND;
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
                .arg(command);
            if recipe_stdin {
                child
                    .arg("--args-file")
                    .arg("-")
                    .stdin(std::process::Stdio::piped());
            } else {
                child
                    .arg("--args-json")
                    .arg(
                        std::str::from_utf8(&encoded)
                            .map_err(|_| invalid("Production arguments are not UTF-8 JSON"))?,
                    )
                    .stdin(std::process::Stdio::null());
            }
            child
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .kill_on_drop(true);

            let budget_secs = if let Some(binding) = binding {
                binding.check_connection(self)?;
                binding.transport_budget_secs(command, &args, mutation)?
            } else if command == "driver.manim-community.doctor" {
                DEFAULT_DEADLINE_SECS
            } else if command.starts_with("driver.hyperframes.")
                || command.starts_with("driver.mlt-video.")
                || command.starts_with("driver.blender.")
                || command.starts_with("driver.manim-community.")
            {
                MLT_DEADLINE_SECS
            } else {
                DEFAULT_DEADLINE_SECS
            };
            if let Some(custody) = &self.transport_custody {
                let process = child.as_std();
                let argv: Vec<_> = process.get_args().collect();
                capture = Some(if recipe_stdin {
                    custody.reserve_graph_recipe(
                        mutation,
                        &self.connection,
                        process.get_program(),
                        argv.iter().copied(),
                        &encoded,
                    )?
                } else {
                    custody.reserve(
                        command,
                        mutation,
                        &self.connection,
                        process.get_program(),
                        argv.iter().copied(),
                        &encoded,
                    )?
                });
            }
            let deadline = Instant::now() + Duration::from_secs(budget_secs);
            let mut child = child.spawn().map_err(|_| {
                Error::new(ErrorCode::Unavailable, "Semwright CLI could not be started")
            })?;
            if let Some(capture) = capture.as_ref() {
                capture.started(child.id());
            }
            // Once the child starts, a transport failure cannot establish whether a
            // mutation ran. A typed Broker error retains its own outcome below.
            let post_spawn_error = |error: Error| {
                if mutation { error.uncertain() } else { error }
            };
            let stdin =
                if recipe_stdin {
                    Some(child.stdin.take().ok_or_else(|| {
                        post_spawn_error(backend("Semwright CLI stdin unavailable"))
                    })?)
                } else {
                    None
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
                        let status = child.wait().await.map_err(|_| {
                            post_spawn_error(backend("Semwright CLI process failed"))
                        })?;
                        if let Some(capture) = capture.as_ref() {
                            capture.process_exit(&status);
                        }
                        Ok::<_, Error>(status)
                    },
                    async {
                        transport_custody::read_bounded_with_capture(
                            stdout,
                            capture.as_ref(),
                            transport_custody::Stream::Stdout,
                        )
                        .await
                        .map_err(|_| {
                            post_spawn_error(backend("Semwright CLI output could not be read"))
                        })
                    },
                    async {
                        transport_custody::read_bounded_with_capture(
                            stderr,
                            capture.as_ref(),
                            transport_custody::Stream::Stderr,
                        )
                        .await
                        .map_err(|_| {
                            post_spawn_error(backend("Semwright CLI diagnostics could not be read"))
                        })
                    },
                    async {
                        if let Some(stdin) = stdin {
                            transport_custody::write_graph_recipe_stdin(
                                stdin,
                                &encoded,
                                capture.as_ref(),
                            )
                            .await
                            .map_err(|_| {
                                post_spawn_error(backend(
                                    "Semwright CLI recipe input could not be completed",
                                ))
                            })?;
                        }
                        Ok::<_, Error>(())
                    },
                )
            })
            .await;
            let (status, stdout, stderr, ()) = match captured {
                Ok(result) => result?,
                Err(_) => {
                    if let Some(capture) = capture.as_ref() {
                        capture.transport_deadline_elapsed();
                    }
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

            let original_core = if retain_original_core {
                Some(serde_json::from_slice::<Value>(&stdout).map_err(|_| {
                    post_spawn_error(backend("Semwright CLI returned invalid Core JSON"))
                })?)
            } else {
                None
            };
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
            if recipe_stdin {
                // The typed consumer authenticates this exact Core envelope and
                // retains errors as well as successes. No nested envelope or
                // provenance is reconstructed from recipe step data.
                let original = original_core.ok_or_else(|| {
                    post_spawn_error(backend("Original Graph recipe envelope is absent"))
                })?;
                if !status.success() || !envelope.ok {
                    recipe_terminal_error = Some(envelope.error.clone().unwrap_or_else(|| {
                        post_spawn_error(backend("Graph recipe failed without a typed error"))
                    }));
                }
                return Ok(BrokerResult {
                    value: json!({
                        "original_envelope": original,
                        "transport_status_success": status.success(),
                    }),
                    request_id: envelope.request_id,
                });
            }
            if !retain_original_core {
                // Ordinary invocations do not request dry-run or admit both result arms.
                // Missing data/error retain their existing diagnostic branches below.
                if envelope.execution.dry_run {
                    return Err(post_spawn_error(backend(
                        "Canonical production response reported an unrequested dry run",
                    )));
                }
                if envelope.error.is_some() && (envelope.ok || envelope.data.is_some()) {
                    return Err(post_spawn_error(backend(
                        "Canonical production response has contradictory result fields",
                    )));
                }
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
                || (expected.provider_generation_required
                    && provenance.provider_generation.is_none())
                || !is_sha256(&provenance.descriptor_sha256)
            {
                return Err(post_spawn_error(backend(
                    "Canonical response provenance does not match the expected authority",
                )));
            }
            let value = envelope.data.ok_or_else(|| {
                post_spawn_error(backend("Canonical production response has no data"))
            })?;
            let mut projected = json!({
                "data": value,
                "execution": envelope.execution,
                "warnings": envelope.warnings,
                "provenance_checked": true
            });
            if let Some(original) = original_core {
                projected["original_envelope"] = original;
            }
            Ok(BrokerResult {
                value: projected,
                request_id: envelope.request_id,
            })
        }
        .await;
        if let Some(capture) = capture.as_mut() {
            if let Some(error) = recipe_terminal_error {
                // Returning the original error envelope for typed inspection
                // must not turn UNKNOWN/failure into successful custody metadata.
                let terminal: NativeResult<BrokerResult> = Err(error);
                capture.finish(&terminal);
            } else {
                capture.finish(&result);
            }
        }
        result
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

fn retained_manim_job_failure(
    data: &Value,
    unknown_state: bool,
    start_command: &'static str,
) -> Error {
    let original = data
        .get("original_error")
        .cloned()
        .and_then(|value| serde_json::from_value::<Error>(value).ok())
        .unwrap_or_else(|| backend("Manim Community original render reported failure"));
    if unknown_state || !original.outcome_known {
        unconfirmed_started_manim_render(original, start_command)
    } else {
        original
    }
}

fn retryable_manim_status_error(error: &Error) -> bool {
    // Only an ordinary read timeout permits another observation of this job.
    // An uncertain error must retain the acknowledged start for reconciliation.
    error.code == ErrorCode::Timeout && error.outcome_known
}

fn require_acknowledged_manim_observation<'a>(
    observation: &'a Value,
    acknowledged_job_ref: &str,
    acknowledged_generation: u64,
    expected_plan_sha256: &str,
    expected_profile: &Value,
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
        || data.get("plan_sha256").and_then(Value::as_str) != Some(expected_plan_sha256)
        || data.get("profile") != Some(expected_profile)
    {
        return Err(unconfirmed_started_manim_render(
            backend(format!(
                "Manim {observation_kind} does not identify the acknowledged native job, generation, plan and profile"
            )),
            start_command,
        ));
    }
    if observation_kind == "start" && data.get("state").and_then(Value::as_str) != Some("rendering")
    {
        return Err(unconfirmed_started_manim_render(
            backend("Manim render start returned an unsupported initial state"),
            start_command,
        ));
    }
    Ok(data)
}

enum PreparedManimSourceInput {
    Editable(crate::manim_editable_projection::OriginalEditableBundleInput),
    Graph(crate::manim_graph_projection::OriginalGraphBundleInput),
}

impl PreparedManimSourceInput {
    fn start_value(&self, command: &str) -> NativeResult<Value> {
        match self {
            Self::Editable(input) if command == "driver.manim-community.editable.render.start" => {
                serde_json::to_value(input)
                    .map_err(|_| invalid("Original editable input cannot be encoded"))
            }
            Self::Graph(input) if command == manim_graph::START_COMMAND => {
                crate::manim_graph_projection::validate_original_manim_graph_input(input)?;
                serde_json::to_value(input)
                    .map_err(|_| invalid("Original Graph input cannot be encoded"))
            }
            _ => Err(Error::new(
                ErrorCode::Unsupported,
                "Original source input does not match the selected native profile",
            )),
        }
    }
}

struct PreparedManimRender {
    start_command: &'static str,
    plan_value: Value,
    plan_sha256: String,
    profile: ManimRenderProfile,
    requires_camera_getters: bool,
    source_bundle: Option<PreparedManimSourceInput>,
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

/// Backend-owned lifetime for the existing bounded receipt custody.
/// Never serialize this handle or expose its private snapshot to the WebView.
/// A retained terminal is not durable, a grant, or permission to redispatch.
#[derive(Clone, Default)]
pub struct ProductionReceiptCustody {
    inner: receipt_custody::ReceiptCustody,
}

impl ProductionReceiptCustody {
    /// Original unconfirmed terminals and storage diagnostics for backend-only
    /// custody. The same existing slots/budget apply across handle clones.
    pub fn private_snapshot(&self) -> Value {
        self.inner.snapshot()
    }
}

/// Private grammar for the durable keys emitted by one existing caller.
/// This is a history selector, not an execution grant or a wire contract.
#[derive(Clone, Copy)]
enum SceneRequestFamily {
    Motion,
    Manim,
    Blender,
    Hyperframes,
}

impl SceneRequestFamily {
    fn contains(self, base: &str, candidate: &str) -> bool {
        fn index(value: &str) -> bool {
            value
                .parse::<usize>()
                .is_ok_and(|parsed| parsed.to_string() == value)
        }
        fn poll(value: &str) -> bool {
            value
                .parse::<u32>()
                .is_ok_and(|parsed| parsed.to_string() == value)
        }
        let Some(suffix) = candidate
            .strip_prefix(base)
            .and_then(|rest| rest.strip_prefix(':'))
        else {
            return false;
        };
        match self {
            Self::Manim => {
                matches!(suffix, "start" | "result")
                    || suffix.strip_prefix("status:").is_some_and(poll)
            }
            Self::Motion => {
                let Some((segment, phase)) = suffix
                    .strip_prefix("motion:")
                    .and_then(|rest| rest.split_once(':'))
                else {
                    return false;
                };
                if !index(segment) {
                    return false;
                }
                if matches!(
                    phase,
                    "font-project-inspect"
                        | "font-project-create"
                        | "plan"
                        | "apply"
                        | "render-start"
                        | "render-result"
                        | "verify"
                ) {
                    return true;
                }
                let Some((phase, counter)) = phase.split_once(':') else {
                    return false;
                };
                (matches!(phase, "font-list" | "font-import" | "font-readback") && index(counter))
                    || (phase == "render-status" && poll(counter))
            }
            Self::Blender => {
                if matches!(suffix, "collection" | "export") {
                    return true;
                }
                let Some((object, phase)) = suffix
                    .strip_prefix("object:")
                    .and_then(|rest| rest.split_once(':'))
                else {
                    return false;
                };
                index(object)
                    && matches!(
                        phase,
                        "create" | "transform" | "link" | "material" | "material-assign"
                    )
            }
            Self::Hyperframes => {
                matches!(
                    suffix,
                    "plan" | "inspect-before" | "apply" | "render-start" | "render-result"
                ) || suffix.strip_prefix("render-status:").is_some_and(poll)
            }
        }
    }
}

fn scene_receipt_snapshot_error(error: motionwright_storage::StorageError) -> Error {
    match error {
        motionwright_storage::StorageError::ReceiptStreamChanged => Error::new(
            ErrorCode::Conflict,
            "Production receipt history changed during admission; inspect before continuing",
        ),
        other => storage_error(other),
    }
}

fn require_no_unresolved_scene_history(
    request_id: &str,
    family: SceneRequestFamily,
    watermark: &motionwright_service::ProductionReceiptWatermark,
    mut read_page: impl FnMut(
        Option<Uuid>,
    ) -> std::result::Result<
        Vec<motionwright_service::ProductionReceipt>,
        motionwright_storage::StorageError,
    >,
    final_watermark: impl FnOnce() -> std::result::Result<
        motionwright_service::ProductionReceiptWatermark,
        motionwright_storage::StorageError,
    >,
) -> NativeResult<()> {
    // The reader supplies pages authenticated against the captured watermark.
    // A positive unresolved head is sufficient to refuse without admitting an
    // effect. Complete history and a stable final watermark are required for Ok.
    let mut before = None;
    let mut inspected = 0_u64;
    let mut heads = std::collections::BTreeSet::new();
    loop {
        let batch = read_page(before).map_err(scene_receipt_snapshot_error)?;
        if batch.is_empty() {
            if inspected != watermark.count {
                return Err(backend("Production receipt history snapshot is incomplete"));
            }
            break;
        }
        inspected = inspected
            .checked_add(
                u64::try_from(batch.len())
                    .map_err(|_| backend("Production receipt page count exceeds its bound"))?,
            )
            .ok_or_else(|| backend("Production receipt history count overflowed"))?;
        if inspected > watermark.count {
            return Err(backend(
                "Production receipt history exceeds its captured watermark",
            ));
        }
        let next = batch.last().map(|receipt| receipt.id);
        if next == before {
            return Err(backend("Production receipt history cursor did not advance"));
        }
        for prior in batch {
            // Head selection precedes classification: a newer known terminal
            // suppresses its older UNKNOWN. Never filter by today's plan/stamp.
            if family.contains(request_id, &prior.request_id)
                && heads.insert(prior.request_id.clone())
                && (prior.stage == "outcome_unknown"
                    || (prior.stage == "dispatching"
                        && prior.payload.get("mutation").and_then(Value::as_bool) == Some(true)))
            {
                // Do not let a later unrelated append or read failure replace
                // this positively observed original uncertainty with known error.
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Production scene request has an unresolved original receipt; inspect before retrying",
                ).uncertain());
            }
        }
        before = next;
        if inspected == watermark.count {
            break;
        }
    }
    if final_watermark().map_err(scene_receipt_snapshot_error)? != *watermark {
        return Err(scene_receipt_snapshot_error(
            motionwright_storage::StorageError::ReceiptStreamChanged,
        ));
    }
    Ok(())
}

// Closed continuation chosen from the committed Film before its first effect.
// One segment uses the direct encoder/mux route; two or more use exactly one
// semantic timeline. These are alternatives, never additive replay allowances.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum MltBudgetRoute {
    Direct,
    Timeline,
}

#[derive(Debug, Clone)]
struct MltEncodeBudget {
    manifest_sha256: String,
    frames: u64,
    output_path: String,
    max_bytes: u64,
}

#[derive(Debug, Clone)]
struct MltDispatchBudget {
    route: MltBudgetRoute,
    width: u32,
    height: u32,
    frames: u64,
    encodes: std::collections::BTreeMap<String, MltEncodeBudget>,
    timeline_paths: Vec<String>,
    mux_video_path: String,
    mux_output_path: Option<String>,
}

impl MltDispatchBudget {
    fn units_for(
        &self,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<RootBudgetUnits> {
        let mut units = RootBudgetUnits {
            attempts: 1,
            model_calls: 0,
            render_frames: 0,
            output_bytes: 0,
        };
        match command {
            "driver.mlt-video.frames.encode" => {
                let source = args
                    .get("manifest_path")
                    .and_then(Value::as_str)
                    .and_then(|path| self.encodes.get(path))
                    .ok_or_else(|| invalid("MLT encoding has no retained source-frame budget"))?;
                if !mutation
                    || args.get("root").and_then(Value::as_str) != Some("output")
                    || args.get("expected_manifest_sha256").and_then(Value::as_str)
                        != Some(source.manifest_sha256.as_str())
                    || args.get("output_path").and_then(Value::as_str)
                        != Some(source.output_path.as_str())
                    || args.get("max_bytes").and_then(Value::as_u64) != Some(source.max_bytes)
                {
                    return Err(invalid(
                        "MLT encoding differs from the retained native plan",
                    ));
                }
                units.render_frames = source.frames;
                units.output_bytes = hybrid_budget::mlt_encode_output_bytes(
                    self.width,
                    self.height,
                    source.frames,
                    &source.output_path,
                    source.max_bytes,
                )?;
            }
            "driver.mlt-video.render.plan" | "driver.mlt-video.render.start" => {
                let output = args.get("output").and_then(Value::as_str);
                let profile = args.get("profile").and_then(Value::as_str);
                if self.route != MltBudgetRoute::Timeline
                    || !self
                        .timeline_paths
                        .iter()
                        .any(|path| Some(path.as_str()) == output)
                    || !matches!(profile, Some("lossless" | "lossless-video-only"))
                    || mutation != (command == "driver.mlt-video.render.start")
                {
                    return Err(invalid(
                        "MLT timeline differs from the retained closed plan",
                    ));
                }
                if mutation {
                    units.render_frames = self.frames;
                    units.output_bytes = hybrid_budget::mlt_timeline_output_bytes(
                        self.width,
                        self.height,
                        self.frames,
                        self.encodes.len() as u64,
                        output.ok_or_else(|| invalid("MLT timeline output is absent"))?,
                    )?;
                }
            }
            "driver.mlt-video.av.mux" => {
                if !mutation
                    || args.get("video_root").and_then(Value::as_str) != Some("output")
                    || args.get("video_path").and_then(Value::as_str)
                        != Some(self.mux_video_path.as_str())
                    || args.get("output_path").and_then(Value::as_str)
                        != self.mux_output_path.as_deref()
                    || self.mux_output_path.is_none()
                    || args.get("frame_count").and_then(Value::as_u64) != Some(self.frames)
                    || args.get("max_bytes").and_then(Value::as_u64) != Some(MLT_MASTER_MAX_BYTES)
                    || args.get("profile").and_then(Value::as_str) != Some("h264-aac-mp4")
                {
                    return Err(invalid("MLT mux differs from the retained native plan"));
                }
                units.render_frames = self.frames;
                units.output_bytes = hybrid_budget::mlt_mux_output_bytes(
                    self.width,
                    self.height,
                    self.frames,
                    self.mux_output_path
                        .as_deref()
                        .ok_or_else(|| invalid("MLT mux output is absent"))?,
                    MLT_MASTER_MAX_BYTES,
                )?;
            }
            "driver.mlt-video.asset.import" => {
                if !mutation
                    || self.route != MltBudgetRoute::Timeline
                    || args.get("kind").and_then(Value::as_str) != Some("file")
                    || args.get("root").and_then(Value::as_str) != Some("output")
                    || !self.encodes.values().any(|source| {
                        args.get("path").and_then(Value::as_str)
                            == Some(source.output_path.as_str())
                    })
                {
                    return Err(invalid("MLT import is outside the retained source plan"));
                }
                // No media is published by import, but its input/probe originals
                // are retained even when the operation fails or outputs are empty.
                units.output_bytes = hybrid_budget::mlt_import_output_bytes()?;
            }
            "driver.mlt-video.project.create"
            | "driver.mlt-video.sequence.create"
            | "driver.mlt-video.track.create"
            | "driver.mlt-video.clip.insert" => {
                if !mutation || self.route != MltBudgetRoute::Timeline {
                    return Err(invalid("MLT edit is outside the retained timeline route"));
                }
                // These closed edits change the provider's in-memory project.
                // File materialization is reserved at import/encode/render/mux.
            }
            "driver.mlt-video.sequence.list"
            | "driver.mlt-video.track.list"
            | "driver.mlt-video.sequence.duration"
            | "driver.mlt-video.render.status"
            | "driver.mlt-video.render.result" => {
                if mutation || self.route != MltBudgetRoute::Timeline {
                    return Err(invalid(
                        "MLT observation is outside the retained timeline route",
                    ));
                }
            }
            "driver.mlt-video.sync.probe" => {
                if mutation || self.route != MltBudgetRoute::Direct {
                    return Err(invalid("MLT sync observation is outside the direct route"));
                }
            }
            _ => {
                return Err(invalid(
                    "Native command has no retained MLT continuation cost",
                ));
            }
        }
        Ok(units)
    }
}

// A native accounting root is neither a Provider grant nor an authenticated
// principal. This private scope is derived solely from the original committed
// project/Film, then every native child must debit SQLite before dispatch.
#[derive(Debug, Clone)]
struct MotionRootBudgetScope {
    root_id: Uuid,
    max_render_frames: u64,
    max_frame_bytes: u64,
    mlt_route: Option<MltBudgetRoute>,
    mlt: Option<MltDispatchBudget>,
    audio_analysis: bool,
}

impl MotionRootBudgetScope {
    fn units_for(
        &self,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<RootBudgetUnits> {
        if command == "driver.audio-analysis.artifact.measure" && self.audio_analysis && !mutation {
            return Ok(RootBudgetUnits {
                attempts: 1,
                model_calls: 0,
                render_frames: 0,
                // The opt-in wrapper stages one bounded original PCM input.
                output_bytes: audio_analysis::MAX_ANALYSIS_BYTES,
            });
        }
        if !MOTION_CANVAS_COMMANDS.contains(&command) {
            if let Some(mlt) = &self.mlt {
                return mlt.units_for(command, args, mutation);
            }
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "A retained Motion Canvas root cannot dispatch another provider",
            ));
        }
        // SQLite schema6 requires a debit even for a new read-only native
        // observation once the project has a root. Only effect-bearing calls
        // additionally reserve frames and output bytes.
        let mut units = RootBudgetUnits {
            attempts: 1,
            model_calls: 0,
            render_frames: 0,
            output_bytes: 0,
        };
        match command {
            "driver.motion-canvas.render.start" => {
                if !mutation {
                    return Err(invalid("Render start requires a mutating SDK permit"));
                }
                let first = args
                    .pointer("/profile/first_frame")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| invalid("Native render start has no first frame"))?;
                let end = args
                    .pointer("/profile/end_frame_exclusive")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| invalid("Native render start has no exclusive end"))?;
                if first != 0 || end <= first || end > self.max_render_frames {
                    return Err(invalid("Native render starts outside retained root frames"));
                }
                units.render_frames = end - first;
                units.output_bytes = units
                    .render_frames
                    .checked_mul(self.max_frame_bytes)
                    .ok_or_else(|| invalid("Native frame byte reservation overflow"))?;
            }
            "driver.motion-canvas.asset.import" => {
                if !mutation {
                    return Err(invalid(
                        "Managed font import requires a mutating SDK permit",
                    ));
                }
                // Source admission already bounds each verified font blob.
                units.output_bytes = MAX_BRAND_FONT_BYTES;
            }
            "driver.motion-canvas.project.create"
            | "driver.motion-canvas.composition.apply"
            | "driver.motion-canvas.render.cancel" => {
                if !mutation {
                    return Err(invalid(
                        "Native source mutation requires a mutating SDK permit",
                    ));
                }
                // Reserve nonzero native source/output capacity before the
                // mutation. Actual output readback still requires G2.
                units.output_bytes = MOTION_ROOT_FRAME_RESERVE_MIN_BYTES;
            }
            _ if mutation => {
                return Err(invalid(
                    "Unclassified Motion Canvas effect has no root debit",
                ));
            }
            _ => {}
        }
        Ok(units)
    }
}

// Private provenance for one already-created accounting root. This record
// cannot authorize provider calls or change SQLite's durable limits/debits.
struct RetainedMotionRoot {
    root: RootOperationBudget,
    source_digest: String,
    scope: MotionRootBudgetScope,
    render_digest: Option<String>,
    // Derived from the original committed Film; never accepted from a caller.
    direct_mlt_scene_scope: Option<std::sync::Arc<crate::native_scene_boundary::ExecutionScope>>,
}

#[derive(Clone)]
pub struct ProductionCoordinator {
    service: StudioService,
    client: ProductionClient,
    receipt_custody: receipt_custody::ReceiptCustody,
    brand_font_media_root: Option<PathBuf>,
    retained_brand_font_root: Option<std::sync::Arc<brand_font_staging::RetainedFontRoot>>,
    scene_boundary_scope: Option<std::sync::Arc<crate::native_scene_boundary::ExecutionScope>>,
    // Clones share one private identity cache. SQLite, not this process memory,
    // is authoritative for cumulative debits and spent UNKNOWN capacity.
    // Pair the retained operation with its original Film-options digest.
    // A fresh process cannot silently reuse or remint the persisted root.
    motion_budget_roots:
        std::sync::Arc<std::sync::Mutex<std::collections::BTreeMap<String, RetainedMotionRoot>>>,
    active_motion_budget: Option<MotionRootBudgetScope>,
    // The mixed-renderer route owns one preflighted root across MC, static
    // Manim and its closed MLT continuation. No scoped client is public.
    active_hybrid_budget: Option<hybrid_budget::HybridRootBudgetScope>,
    // Observation of the existing CI owner's project grant. This adds no
    // filesystem grant or arbitrary project path to ProductionConnection.
    hybrid_project_grant: Option<std::sync::Arc<hybrid_budget::HybridProjectGrant>>,
    #[cfg(test)]
    before_dispatch_admission: Option<std::sync::Arc<dyn Fn() + Send + Sync>>,
}

impl ProductionCoordinator {
    pub fn new(service: StudioService, connection: ProductionConnection) -> NativeResult<Self> {
        Self::with_receipt_custody(service, connection, &ProductionReceiptCustody::default())
    }

    /// Reuse application-owned custody without keeping a stale connection or
    /// creating another receipt store. Each connection is still validated.
    pub fn with_receipt_custody(
        service: StudioService,
        connection: ProductionConnection,
        custody: &ProductionReceiptCustody,
    ) -> NativeResult<Self> {
        Ok(Self {
            service,
            client: ProductionClient::new(connection)?,
            receipt_custody: custody.inner.clone(),
            brand_font_media_root: None,
            retained_brand_font_root: None,
            scene_boundary_scope: None,
            motion_budget_roots: std::sync::Arc::new(std::sync::Mutex::new(
                std::collections::BTreeMap::new(),
            )),
            active_motion_budget: None,
            active_hybrid_budget: None,
            hybrid_project_grant: None,
            #[cfg(test)]
            before_dispatch_admission: None,
        })
    }

    fn require_no_unresolved_scene_requests(
        &self,
        project_id: Uuid,
        request_id: &str,
        family: SceneRequestFamily,
    ) -> NativeResult<()> {
        let watermark = self
            .service
            .production_receipt_watermark(project_id)
            .map_err(scene_receipt_snapshot_error)?;
        require_no_unresolved_scene_history(
            request_id,
            family,
            &watermark,
            |before| {
                self.service
                    .production_receipts_snapshot_page(project_id, &watermark, before, 256)
            },
            || self.service.production_receipt_watermark(project_id),
        )
    }

    fn with_motion_scene_boundary_scope(
        &self,
        project: &motionwright_domain::Project,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        scene_ids: &[Uuid],
    ) -> NativeResult<Self> {
        let scope = crate::native_scene_boundary::ExecutionScope::motion(
            project,
            deliverable_id,
            options,
            scene_ids,
        )?;
        // Admission to this private accounting identity must be bound to the
        // original project connection before it can write a durable root.
        // This does not replace genuine authenticated Host/SDK permission.
        if self.client.connection().resource != project.resource_key() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Native root provider binding differs from the original project",
            ));
        }
        let _bound_connection = self.client.connection().identity()?;

        if let Some(hybrid) = &self.active_hybrid_budget {
            hybrid.require_motion_selection(self, project, deliverable_id, options, scene_ids)?;
            let mut scoped = self.clone();
            scoped.scene_boundary_scope = Some(std::sync::Arc::new(scope));
            return Ok(scoped);
        }

        // Replan from the exact committed project before constructing ANY
        // Motion Canvas provider effect. No client JSON supplies root/limits.
        let segments = plan_motion_canvas_segments(project, deliverable_id, options)?;
        if segments.is_empty() {
            return Err(invalid(
                "Budgeted Motion Canvas root has no native segments",
            ));
        }
        let segment_count = u64::try_from(segments.len())
            .map_err(|_| invalid("Too many Motion Canvas budget segments"))?;
        let mut total_frames = 0_u64;
        let mut frame_reservation = MOTION_ROOT_FRAME_RESERVE_MIN_BYTES;
        for segment in &segments {
            if segment.frame_count == 0
                || segment.film.output.width == 0
                || segment.film.output.height == 0
            {
                return Err(invalid("Budgeted Motion Canvas Film is empty"));
            }
            total_frames = total_frames
                .checked_add(segment.frame_count)
                .ok_or_else(|| invalid("Motion Canvas cumulative frame overflow"))?;
            let frame_pixels = u64::from(segment.film.output.width)
                .checked_mul(u64::from(segment.film.output.height))
                .ok_or_else(|| invalid("Motion Canvas output pixel count overflow"))?;
            let frame_rgba = frame_pixels
                .checked_mul(4)
                .and_then(|count| count.checked_add(1_048_576))
                .ok_or_else(|| invalid("Motion Canvas frame reservation overflow"))?;
            frame_reservation = frame_reservation.max(frame_rgba);
        }
        // Polling is bounded by this producer's real timeout and interval.
        let bounded_polls = MOTION_RENDER_POLL_DEADLINE_SECS
            .checked_mul(1_000)
            .and_then(|duration| duration.checked_div(MOTION_RENDER_POLL_INTERVAL_MS))
            .and_then(|polls| polls.checked_add(MOTION_ROOT_NON_POLL_ATTEMPTS_PER_SEGMENT))
            .ok_or_else(|| invalid("Motion Canvas poll budget overflow"))?;
        let attempts = segment_count
            .checked_mul(bounded_polls)
            .ok_or_else(|| invalid("Motion Canvas attempt budget overflow"))?;
        let render_bytes = total_frames
            .checked_mul(frame_reservation)
            .ok_or_else(|| invalid("Motion Canvas render budget byte overflow"))?;
        let font_bytes = segment_count
            .checked_mul(2)
            .and_then(|count| count.checked_mul(MAX_BRAND_FONT_BYTES))
            .ok_or_else(|| invalid("Motion Canvas font budget overflow"))?;
        let source_bytes = segment_count
            .checked_mul(3)
            .and_then(|stages| stages.checked_mul(MOTION_ROOT_FRAME_RESERVE_MIN_BYTES))
            .ok_or_else(|| invalid("Motion Canvas source mutation budget overflow"))?;
        let output_bytes = render_bytes
            .checked_add(font_bytes)
            .and_then(|bytes| bytes.checked_add(source_bytes))
            .ok_or_else(|| invalid("Motion Canvas cumulative byte budget overflow"))?;
        // Reserve the one supported continuation before the first MC effect.
        // Direct: encode + mux + optional sync, two additional frame passes.
        // Timeline: 5N + 9 fixed calls including N encodes and one mux, at most
        // 330 status calls, three additional frame passes. Publications and
        // SDK owned/raw originals share the same closed costs as dispatch.
        // Choosing PCM or video-only consumes the same single timeline allowance.
        let profile = project
            .deliverables
            .iter()
            .find(|p| p.id == deliverable_id)
            .ok_or_else(|| invalid("Native root deliverable is unavailable"))?;
        let all_motion = project
            .scenes
            .iter()
            .filter(|scene| {
                profile.included_scene_ids.is_empty()
                    || profile.included_scene_ids.contains(&scene.id)
            })
            .all(|scene| scene.renderer == motionwright_domain::RendererKind::MotionCanvas);
        let mlt_admitted = all_motion
            && total_frames <= 36_000
            && segment_count <= 64
            && profile.video_codec == VideoCodec::H264
            && profile.audio_codec == AudioCodec::Aac
            && profile.audio_sample_rate_hz == 48_000
            && profile.container == OutputContainer::Mp4
            && profile.color_space == OutputColorSpace::Rec709
            && profile.frame_rate.num == i64::from(options.frame_rate.num)
            && profile.frame_rate.den == i64::from(options.frame_rate.den)
            && (segment_count == 1
                || matches!((profile.width, profile.height), (1280, 720) | (1920, 1080)))
            // An unsupported MLT observation must not prevent an otherwise
            // valid MC capture. Reserve this continuation only when the SDK
            // can admit its complete output and each source encode.
            && semwright_video_domain::native_observation::observation_budget(
                profile.width, profile.height, total_frames,
            ).is_ok()
            && segments.iter().all(|segment| {
                semwright_video_domain::native_observation::observation_budget(
                    profile.width, profile.height, segment.frame_count,
                ).is_ok()
            });
        let mlt_route = mlt_admitted.then_some(if segment_count == 1 {
            MltBudgetRoute::Direct
        } else {
            MltBudgetRoute::Timeline
        });
        let mut limits = RootBudgetUnits {
            attempts,
            model_calls: 0,
            render_frames: total_frames,
            output_bytes,
        };
        if let Some(route) = mlt_route {
            let timeline = route == MltBudgetRoute::Timeline;
            let continuation_calls = if timeline {
                segment_count
                    .checked_mul(5)
                    .and_then(|n| n.checked_add(9 + 330))
            } else {
                Some(3)
            }
            .ok_or_else(|| invalid("MLT continuation call reservation overflow"))?;
            let continuation_frames = total_frames
                .checked_mul(if timeline { 3 } else { 2 })
                .ok_or_else(|| invalid("MLT continuation frame reservation overflow"))?;
            // Output tokens are selected later. Their fixed digest widths and
            // this saved revision already determine the exact metadata bound;
            // these placeholders reserve lengths and never name an output.
            let direct_stem = format!(
                "mw-{}-r{}-{}",
                project.id.simple(),
                project.revision,
                "0".repeat(16),
            );
            let timeline_stem = format!("mw-mlt-{}", "0".repeat(20));
            let mut intermediates = 0_u64;
            for (index, segment) in segments.iter().enumerate() {
                let output = if timeline {
                    format!("{timeline_stem}-s{index:02}-ffv1.mkv")
                } else {
                    format!("{direct_stem}-motion.mkv")
                };
                let bytes = hybrid_budget::mlt_encode_output_bytes(
                    profile.width,
                    profile.height,
                    segment.frame_count,
                    &output,
                    MLT_MEZZANINE_MAX_BYTES,
                )?;
                intermediates = intermediates
                    .checked_add(bytes)
                    .ok_or_else(|| invalid("MLT intermediate reservation overflow"))?;
            }
            let master_path = if timeline {
                format!("{direct_stem}-multisegment-master.mp4")
            } else {
                format!("{direct_stem}-master.mp4")
            };
            let mut final_outputs = hybrid_budget::mlt_mux_output_bytes(
                profile.width,
                profile.height,
                total_frames,
                &master_path,
                MLT_MASTER_MAX_BYTES,
            )?;
            if timeline {
                let imports = hybrid_budget::mlt_import_output_bytes()?
                    .checked_mul(segment_count)
                    .ok_or_else(|| invalid("MLT import original reservation overflow"))?;
                let render = hybrid_budget::mlt_timeline_output_bytes(
                    profile.width,
                    profile.height,
                    total_frames,
                    segment_count,
                    &format!("{timeline_stem}-sequence-video-only-ffv1.mkv"),
                )?;
                final_outputs = final_outputs
                    .checked_add(imports)
                    .and_then(|bytes| bytes.checked_add(render))
                    .ok_or_else(|| invalid("MLT final output reservation overflow"))?;
            }
            // Optional source + decoded-PCM measurements use at most two
            // existing meter calls and two bounded staged input files.
            let continuation_calls = continuation_calls
                .checked_add(2)
                .ok_or_else(|| invalid("Native audio observation reservation overflow"))?;
            let audio_staging = audio_analysis::MAX_ANALYSIS_BYTES
                .checked_mul(2)
                .ok_or_else(|| invalid("Native audio staging reservation overflow"))?;
            limits.attempts = limits
                .attempts
                .checked_add(continuation_calls)
                .ok_or_else(|| invalid("Native cumulative call reservation overflow"))?;
            limits.render_frames = limits
                .render_frames
                .checked_add(continuation_frames)
                .ok_or_else(|| invalid("Native cumulative frame reservation overflow"))?;
            limits.output_bytes = limits
                .output_bytes
                .checked_add(intermediates)
                .and_then(|n| n.checked_add(final_outputs))
                .and_then(|n| n.checked_add(audio_staging))
                .ok_or_else(|| invalid("Native cumulative output reservation overflow"))?;
        }
        // The operation key deliberately excludes per-stage and caller request
        // IDs: changing a retry label cannot mint a new root for this revision.
        let operation_key = format!(
            "motion-canvas/{}/{}/{}/{}",
            project.id, project.generation, project.revision, deliverable_id
        );
        // Do not vary the durable root key with input options: different
        // options must not mint fresh budget for the same project revision.
        let source_digest = request_digest("motion-canvas.original-film-options", &json!(options))?;
        let mut cache = self
            .motion_budget_roots
            .lock()
            .map_err(|_| backend("Native budget root ownership is unavailable"))?;
        let retained = if let Some(previous) = cache.get(&operation_key) {
            if previous.source_digest != source_digest {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Original Motion Canvas Film options differ from retained budget source",
                ));
            }
            self.service
                .root_operation_budget(project.id, previous.root.root_id)
                .map_err(storage_error)?
        } else {
            let created = self
                .service
                .create_root_operation_budget(RootOperationBudgetInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    operation_key: operation_key.clone(),
                    limits,
                })
                .map_err(storage_error)?;
            let original_scope = MotionRootBudgetScope {
                root_id: created.root_id,
                max_render_frames: total_frames,
                max_frame_bytes: frame_reservation,
                mlt_route,
                mlt: None,
                audio_analysis: false,
            };
            cache.insert(
                operation_key.clone(),
                RetainedMotionRoot {
                    root: created.clone(),
                    source_digest,
                    scope: original_scope,
                    render_digest: None,
                    direct_mlt_scene_scope: (mlt_route == Some(MltBudgetRoute::Direct)).then(
                        || {
                            // Derive the complete Film selection, including when this
                            // root was first attached by a partial-window invocation.
                            let original_scene_ids = segments
                                .iter()
                                .flat_map(|segment| segment.scene_ids.iter().copied())
                                .collect::<Vec<_>>();
                            std::sync::Arc::new(
                                crate::native_scene_boundary::ExecutionScope::mlt_motion(
                                    project,
                                    deliverable_id,
                                    options,
                                    &original_scene_ids,
                                ),
                            )
                        },
                    ),
                },
            );
            created
        };
        if retained.operation_key != operation_key
            || retained.generation != project.generation
            || retained.revision != project.revision
            || retained.limits != limits
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Native root no longer identifies the same retained Film budget",
            ));
        }
        drop(cache);

        let mut scoped = self.clone();
        scoped.scene_boundary_scope = Some(std::sync::Arc::new(scope));
        scoped.active_motion_budget = Some(MotionRootBudgetScope {
            root_id: retained.root_id,
            max_render_frames: total_frames,
            max_frame_bytes: frame_reservation,
            mlt_route,
            mlt: None,
            audio_analysis: false,
        });
        Ok(scoped)
    }

    fn with_scene_without_film_options(
        &self,
        project: &motionwright_domain::Project,
        scene_id: Uuid,
        renderer: motionwright_domain::RendererKind,
    ) -> NativeResult<Self> {
        let scope = crate::native_scene_boundary::ExecutionScope::scene_without_film_options(
            project, scene_id, renderer,
        )?;
        let mut scoped = self.clone();
        scoped.scene_boundary_scope = Some(std::sync::Arc::new(scope));
        Ok(scoped)
    }

    fn retain_motion_budget_output(
        &self,
        project: &motionwright_domain::Project,
        deliverable_id: Uuid,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<()> {
        if let Some(hybrid) = &self.active_hybrid_budget {
            return hybrid.verify_motion_output(self, project, deliverable_id, rendered);
        }
        let key = format!(
            "motion-canvas/{}/{}/{}/{}",
            project.id, project.generation, project.revision, deliverable_id
        );
        let digest = request_digest("motion-canvas.original-render", &json!(rendered))?;
        let mut cache = self
            .motion_budget_roots
            .lock()
            .map_err(|_| backend("Native budget root ownership is unavailable"))?;
        let original = cache
            .get_mut(&key)
            .ok_or_else(|| backend("Original native accounting root is unavailable"))?;
        if original
            .render_digest
            .as_ref()
            .is_some_and(|prior| prior != &digest)
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Original native output cannot be replaced under the same budget",
            ));
        }
        original.render_digest = Some(digest);
        Ok(())
    }

    fn retained_motion_continuation(
        &self,
        project: &motionwright_domain::Project,
        deliverable_id: Uuid,
        options: Option<&FilmBuildOptions>,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<MotionRootBudgetScope> {
        if self.client.connection().resource != project.resource_key()
            || rendered.project_resource != project.resource_key()
            || rendered.generation != project.generation
            || rendered.revision != project.revision
            || rendered.deliverable_id != deliverable_id
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "MLT continuation does not identify the original native project",
            ));
        }
        let key = format!(
            "motion-canvas/{}/{}/{}/{}",
            project.id, project.generation, project.revision, deliverable_id
        );
        let digest = request_digest("motion-canvas.original-render", &json!(rendered))?;
        let cache = self
            .motion_budget_roots
            .lock()
            .map_err(|_| backend("Native budget root ownership is unavailable"))?;
        let original = cache.get(&key).ok_or_else(|| {
            Error::new(
                ErrorCode::Conflict,
                "Original native budget is not attached; reconcile without a new root",
            )
        })?;
        if original.render_digest.as_deref() != Some(digest.as_str())
            || original.scope.mlt_route.is_none()
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "MLT continuation lacks the retained original render and route",
            ));
        }
        if let Some(options) = options
            && request_digest("motion-canvas.original-film-options", &json!(options))?
                != original.source_digest
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "MLT continuation changed the original Film options",
            ));
        }
        let retained = self
            .service
            .root_operation_budget(project.id, original.root.root_id)
            .map_err(storage_error)?;
        if retained.operation_key != key
            || retained.generation != project.generation
            || retained.revision != project.revision
            || retained.limits != original.root.limits
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "MLT continuation lost its immutable original root",
            ));
        }
        Ok(original.scope.clone())
    }

    fn with_mlt_motion_scene_boundary_scope(
        &self,
        project: &motionwright_domain::Project,
        options: &FilmBuildOptions,
        checked: &crate::assembly::MultiSegmentAssemblyPreflight,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<Self> {
        let recipe = crate::mlt_edit_plan::mlt_native_video_recipe(checked)?;
        let mut root = self.retained_motion_continuation(
            project,
            checked.deliverable_id,
            Some(options),
            rendered,
        )?;
        if root.mlt_route != Some(MltBudgetRoute::Timeline)
            || root.max_render_frames != recipe.total_frames
        {
            return Err(invalid(
                "MLT timeline differs from the original native budget plan",
            ));
        }
        let scene_ids = checked
            .segments
            .iter()
            .flat_map(|segment| segment.scene_ids.iter().copied())
            .collect::<Vec<_>>();
        let scope = crate::native_scene_boundary::ExecutionScope::mlt_motion(
            project,
            checked.deliverable_id,
            options,
            &scene_ids,
        );
        let video_only = format!(
            "{}-sequence-video-only-ffv1.mkv",
            recipe.owner_output_namespace
        );
        root.mlt = Some(MltDispatchBudget {
            route: MltBudgetRoute::Timeline,
            width: recipe.provider_profile.width,
            height: recipe.provider_profile.height,
            frames: recipe.total_frames,
            encodes: recipe
                .clips
                .iter()
                .map(|clip| {
                    (
                        clip.source_manifest_path.clone(),
                        MltEncodeBudget {
                            manifest_sha256: clip.source_manifest_sha256.clone(),
                            frames: clip.source_out_exclusive,
                            output_path: clip.ffv1_output_path.clone(),
                            max_bytes: clip.ffv1_max_bytes,
                        },
                    )
                })
                .collect(),
            timeline_paths: vec![recipe.lossless_sequence_path, video_only.clone()],
            mux_video_path: video_only,
            mux_output_path: None,
        });
        let mut scoped = self.clone();
        scoped.scene_boundary_scope = Some(std::sync::Arc::new(scope));
        scoped.active_motion_budget = Some(root);
        Ok(scoped)
    }

    fn with_retained_motion_audio_scope(
        &self,
        project_id: Uuid,
        deliverable_id: Uuid,
        options: Option<&FilmBuildOptions>,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<Self> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        let mut root =
            self.retained_motion_continuation(&project, deliverable_id, options, rendered)?;
        root.audio_analysis = true;
        let mut scoped = self.clone();
        scoped.active_motion_budget = Some(root);
        Ok(scoped)
    }

    fn bind_mlt_mux_output(&mut self, output_path: &str) -> NativeResult<()> {
        let budget = self
            .active_motion_budget
            .as_mut()
            .and_then(|root| root.mlt.as_mut())
            .ok_or_else(|| invalid("MLT mux has no retained private accounting scope"))?;
        budget.mux_output_path = Some(output_path.to_owned());
        Ok(())
    }

    fn recheck_scene_boundary_before_dispatch(
        &self,
        prepared: &motionwright_domain::Project,
        command: &str,
        args: &Value,
    ) -> NativeResult<()> {
        let scope = self.scene_boundary_scope.as_deref();
        let mlt_render_profile = args.get("profile").and_then(Value::as_str);
        if !crate::native_scene_boundary::is_new_scene_effect(command, scope, mlt_render_profile) {
            return Ok(());
        }
        let current = self.service.project(prepared.id).map_err(storage_error)?;
        if current != *prepared {
            return Err(crate::native_scene_boundary::stale());
        }
        crate::native_scene_boundary::guard_new_effect(&current, command, scope, mlt_render_profile)
    }

    fn require_motion_staging_admission(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
    ) -> NativeResult<()> {
        let current = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != current.resource_key()
            || expected.generation != current.generation
            || expected.revision != current.revision
        {
            return Err(crate::native_scene_boundary::stale());
        }
        crate::native_scene_boundary::guard_new_effect(
            &current,
            "driver.motion-canvas.asset.import",
            self.scene_boundary_scope.as_deref(),
            None,
        )
    }

    /// Private, non-durable custody of a terminal whose local append was not
    /// confirmed. This can contain native payloads and storage paths; place it
    /// only in the producer's existing private evidence, never public output.
    /// Inspection neither acknowledges an effect nor permits a redispatch.
    pub fn private_receipt_custody_snapshot(&self) -> Value {
        self.receipt_custody.snapshot()
    }

    /// Attach private byte custody to the same existing production client.
    pub fn with_transport_custody(mut self, custody: &ProductionTransportCustody) -> Self {
        self.client = self.client.with_transport_custody(custody);
        self
    }

    pub fn receipts(&self, project_id: Uuid, limit: usize) -> NativeResult<Vec<ProductionReceipt>> {
        self.service
            .production_receipts(project_id, limit)
            .map_err(storage_error)
    }

    /// Shared provider sequence. Full admission remains in its original caller;
    /// Window admission retains a distinct partial measurement and never emits
    /// a MotionCanvasRenderEvidence.
    async fn capture_motion_canvas_segment(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        prefix: &str,
        index: usize,
        segment: &crate::film::MotionCanvasSegment,
        capture_end: u64,
    ) -> NativeResult<motion_canvas_window::MotionCanvasSegmentCapture> {
        self.require_motion_staging_admission(project_id, expected)?;
        // Register the accepted scope before its first await. A dropped future
        // leaves Running in the AppState-owned custody; it never retires files.
        let custody = self.begin_retained_font_capture(project_id, expected, prefix)?;
        let result = self
            .capture_motion_canvas_segment_inner(
                project_id,
                expected,
                prefix,
                index,
                segment,
                capture_end,
            )
            .await;
        if let Some(custody) = custody {
            custody.finish(&result);
        }
        result
    }

    async fn capture_motion_canvas_segment_inner(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        prefix: &str,
        index: usize,
        segment: &crate::film::MotionCanvasSegment,
        capture_end: u64,
    ) -> NativeResult<motion_canvas_window::MotionCanvasSegmentCapture> {
        if capture_end == 0 || capture_end > segment.frame_count {
            return Err(invalid(
                "Native capture prefix is outside its canonical Film",
            ));
        }
        let phase_started = std::time::Instant::now();
        let film = serde_json::to_value(&segment.film)
            .map_err(|_| invalid("Canonical Film could not be encoded"))?;
        self.prepare_motion_fonts(project_id, expected, prefix, &segment.film)
            .await?;

        self.revalidate_retained_brand_font_staging()?;
        let fonts_micros = motion_canvas_window::elapsed_micros(phase_started);
        let phase_started = std::time::Instant::now();
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
        self.revalidate_retained_brand_font_staging()?;
        let plan_ref = required_string(
            response_data(&planned)?,
            "/plan_ref",
            "Motion Canvas composition plan returned no plan reference",
        )?;

        let plan_micros = motion_canvas_window::elapsed_micros(phase_started);
        let phase_started = std::time::Instant::now();
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
        self.revalidate_retained_brand_font_staging()?;
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

        if let Some(hybrid) = &self.active_hybrid_budget {
            hybrid.retain_motion_source(self, index, segment, prefix, &fingerprint, &applied)?;
        }

        let apply_micros = motion_canvas_window::elapsed_micros(phase_started);
        let phase_started = std::time::Instant::now();
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
                        "end_frame_exclusive": capture_end,
                        "scale": "full",
                        "transparent": false,
                        "timeout_ms": MOTION_RENDER_TIMEOUT_MS
                    }
                }),
                true,
            )
            .await?;
        self.revalidate_retained_brand_font_staging()
            .map_err(unconfirmed_started_motion_render)?;
        let start_origin = match started.get("replayed").and_then(Value::as_bool) {
            Some(false) => MotionCanvasStartOrigin::FreshAuthenticatedCall,
            Some(true) => MotionCanvasStartOrigin::StoredReceiptReplay,
            None => {
                return Err(unconfirmed_started_motion_render(backend(
                    "Native start receipt origin is absent",
                )));
            }
        };
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
                    poll = poll
                        .checked_add(1)
                        .ok_or_else(|| backend("Motion Canvas render poll counter overflowed"))?;
                    sleep(Duration::from_millis(MOTION_RENDER_POLL_INTERVAL_MS)).await;
                    continue;
                }
                Err(error) => return Err(unconfirmed_started_motion_render(error)),
            };
            self.revalidate_retained_brand_font_staging()
                .map_err(unconfirmed_started_motion_render)?;
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
            != Some(capture_end)
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
        self.revalidate_retained_brand_font_staging()
            .map_err(unconfirmed_started_motion_render)?;
        let rendered_data = response_data(&rendered).map_err(unconfirmed_started_motion_render)?;
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
        if artifact.get("frame_count").and_then(Value::as_u64) != Some(capture_end) {
            return Err(unconfirmed_started_motion_render(backend(
                "Motion Canvas result artifact frame count does not match the Film realization",
            )));
        }

        let render_and_wait_micros = motion_canvas_window::elapsed_micros(phase_started);
        let phase_started = std::time::Instant::now();
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
        self.revalidate_retained_brand_font_staging()?;
        let verification = response_data(&verified)?.clone();

        let verification_micros = motion_canvas_window::elapsed_micros(phase_started);
        Ok(motion_canvas_window::MotionCanvasSegmentCapture {
            plan_ref,
            fingerprint,
            job_ref,
            artifact,
            verification,
            start_origin,
            timings: MotionCanvasCaptureTimings {
                fonts_micros,
                plan_micros,
                apply_micros,
                render_and_wait_micros,
                verification_micros,
            },
        })
    }

    /// Constructing this Future accepts the complete retained-font invocation,
    /// even when the caller queues or drops it before its first poll.
    pub fn render_motion_canvas_segments<'a>(
        &'a self,
        project_id: Uuid,
        expected: &'a RevisionStamp,
        request_id: &'a str,
        deliverable_id: Uuid,
        options: &'a FilmBuildOptions,
    ) -> impl std::future::Future<Output = NativeResult<MotionCanvasRenderEvidence>> + Send + 'a
    {
        let accepted = self.accept_retained_font_render(
            project_id,
            expected,
            request_id,
            ProductionFontInvocationKind::Full,
        );
        async move {
            let mut work = accepted?;
            if let Some(work) = work.as_mut() {
                work.start_native()?;
            }
            let result = self
                .render_motion_canvas_segments_inner(
                    project_id,
                    expected,
                    request_id,
                    deliverable_id,
                    options,
                )
                .await;
            if let Some(work) = work {
                work.finish_native(&result);
            }
            result
        }
    }

    async fn render_motion_canvas_segments_inner(
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

        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Motion,
        )?;

        let captions_requested = project
            .deliverables
            .iter()
            .any(|profile| profile.id == deliverable_id && profile.burn_in_captions);
        let segments = plan_motion_canvas_segments(&project, deliverable_id, options)?;
        if segments.is_empty() {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "The selected revision has no Motion Canvas segments to render",
            ));
        }

        let scene_ids = segments
            .iter()
            .flat_map(|segment| segment.scene_ids.iter().copied())
            .collect::<Vec<_>>();
        let scoped =
            self.with_motion_scene_boundary_scope(&project, deliverable_id, options, &scene_ids)?;
        let mut evidence = Vec::with_capacity(segments.len());
        for (index, segment) in segments.into_iter().enumerate() {
            let prefix = format!("{request_id}:motion:{index}");
            let capture = scoped
                .capture_motion_canvas_segment(
                    project_id,
                    expected,
                    &prefix,
                    index,
                    &segment,
                    segment.frame_count,
                )
                .await?;
            let verification = capture.verification;
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
                plan_ref: capture.plan_ref,
                fingerprint: capture.fingerprint,
                job_ref: capture.job_ref,
                artifact: capture.artifact,
                verification,
            });
        }

        let rendered = MotionCanvasRenderEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            frame_rate: options.frame_rate,
            segments: evidence,
        };
        self.retain_motion_budget_output(&project, deliverable_id, &rendered)?;
        Ok(rendered)
    }

    pub async fn assemble_mlt_av_master(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltAvMasterRequest<'_>,
    ) -> NativeResult<MltAvMasterEvidence> {
        self.assemble_mlt_av_master_checked(project_id, expected, request, None)
            .await
    }

    pub(super) async fn assemble_mlt_av_master_checked(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltAvMasterRequest<'_>,
        measured_source: Option<&audio_analysis::VerifiedAudioMeasurement>,
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
        audio_analysis::admit_profile_audio_targets(
            self,
            &project,
            deliverable_id,
            request_id,
            audio_analysis::AudioAnalysisLane::SingleSegment,
            measured_source,
        )?;
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
        let consumes_sfx = !project
            .validated_sfx_schedule_for_profile(deliverable_id)
            .map_err(|error| invalid(error.to_string()))?
            .is_empty();
        if (!has_saved_mix && consumes_sfx)
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
            let mix = crate::original_mix::prepare_saved_original_mix_with_budget(
                &self.service,
                project_id,
                expected,
                deliverable_id,
                motionwright_service::OriginalMixDecodeBudget::FullCut180Seconds,
            )
            .map_err(crate::original_mix::MixError::into_native)?;
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

        let mut root = self.retained_motion_continuation(&project, deliverable_id, None, motion)?;
        if root.mlt_route != Some(MltBudgetRoute::Direct)
            || root.max_render_frames != segment.frame_count
        {
            return Err(invalid(
                "MLT direct route differs from the original native budget plan",
            ));
        }
        root.mlt = Some(MltDispatchBudget {
            route: MltBudgetRoute::Direct,
            width: deliverable.width,
            height: deliverable.height,
            frames: segment.frame_count,
            encodes: std::collections::BTreeMap::from([(
                manifest_path.clone(),
                MltEncodeBudget {
                    manifest_sha256: manifest_sha256.clone(),
                    frames: segment.frame_count,
                    output_path: mezzanine_path.clone(),
                    max_bytes: MLT_MEZZANINE_MAX_BYTES,
                },
            )]),
            timeline_paths: Vec::new(),
            mux_video_path: mezzanine_path.clone(),
            mux_output_path: Some(master_path.clone()),
        });
        // The direct API has no FilmOptions argument. Recover its scene
        // boundary only from the same original root whose rendered evidence
        // was checked above; do not reconstruct authority from client JSON.
        let original_scene_scope = {
            let key = format!(
                "motion-canvas/{}/{}/{}/{}",
                project.id, project.generation, project.revision, deliverable_id
            );
            let cache = self
                .motion_budget_roots
                .lock()
                .map_err(|_| backend("Native budget root ownership is unavailable"))?;
            let original = cache.get(&key).ok_or_else(|| {
                Error::new(
                    ErrorCode::Conflict,
                    "Original direct MLT scene scope is unavailable",
                )
            })?;
            if original.root.root_id != root.root_id
                || original.root.generation != project.generation
                || original.root.revision != project.revision
            {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Direct MLT scene scope differs from the original native root",
                ));
            }
            original
                .direct_mlt_scene_scope
                .clone()
                .ok_or_else(|| invalid("Original Film did not admit the direct MLT scene route"))?
        };
        let mut scoped = self.clone();
        scoped.scene_boundary_scope = Some(original_scene_scope);
        scoped.active_motion_budget = Some(root);
        let encoded = scoped
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

        let muxed = scoped
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
        mlt_multisegment_av::verify_exact_native_mux(
            &master,
            &mlt_multisegment_av::MuxExpected {
                frames: segment.frame_count,
                width: deliverable.width,
                height: deliverable.height,
                fps_num: motion.frame_rate.num,
                fps_den: motion.frame_rate.den,
                source_video: &mezzanine_sha256,
                source_audio: &audio.sha256,
                output_path: &master_path,
            },
        )?;
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
        for key in ["artifact", "decoded_audio"] {
            mlt_multisegment_av::verify_mux_artifact_file(
                &self.client.connection().output_root,
                master
                    .get(key)
                    .ok_or_else(|| backend("MLT mux artifact evidence is missing"))?,
            )?;
        }

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
            let probed = scoped
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
        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Blender,
        )?;
        let plan = build_blender_contribution(&project, scene_id)?;
        let scoped = self.with_scene_without_film_options(
            &project,
            scene_id,
            motionwright_domain::RendererKind::Blender,
        )?;
        let collection = scoped
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
            let object = scoped
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
            let transform = scoped
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
            let link = scoped
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
            let material = scoped
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
            let assignment = scoped
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
        let export = scoped
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
        let deliverable_id =
            crate::manim_static_projection::select_saved_manim_deliverable_for_profile(
                &project, expected, scene_id, &profile,
            )?;
        manim_graph::require_non_graph_selection(&project, scene_id, Some(deliverable_id))?;
        if project
            .production_design
            .camera_reframes
            .iter()
            .any(|row| row.scene_id == scene_id)
        {
            return self
                .realize_manim_camera_scene(
                    project_id,
                    expected,
                    request_id,
                    scene_id,
                    deliverable_id,
                )
                .await;
        }
        if crate::manim_editable_projection::requires_editable_profile(
            &project,
            scene_id,
            deliverable_id,
        ) {
            return self
                .realize_manim_editable_scene(
                    project_id,
                    expected,
                    request_id,
                    scene_id,
                    deliverable_id,
                )
                .await;
        }
        self.realize_manim_static_scene(project_id, expected, request_id, scene_id, deliverable_id)
            .await
    }

    /// Explicit compatibility projection for the V1 wire contract. This route
    /// has no canonical clock or editable-source proof and is not the default
    /// Project realization entrypoint. Callers must select it by name.
    pub async fn realize_legacy_manim_scene(
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

        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Manim,
        )?;
        let scoped = self.with_scene_without_film_options(
            &project,
            scene_id,
            motionwright_domain::RendererKind::ManimCommunity,
        )?;
        manim_graph::require_non_graph_selection(&project, scene_id, None)?;
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

        scoped
            .realize_prepared_manim_scene(
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
                    source_bundle: None,
                },
            )
            .await
    }

    /// Explicit static V2 production from the current canonical snapshot.
    /// The legacy command remains a separate compatibility route; a duration,
    /// source package or native getter proof is never invented for a V1 request.
    pub async fn realize_manim_static_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
        deliverable_id: Uuid,
    ) -> NativeResult<Value> {
        validate_manim_request_id(request_id)?;
        manim_graph::require_non_graph_saved_selection(
            &self.service,
            project_id,
            expected,
            scene_id,
            deliverable_id,
        )?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Manim,
        )?;
        let scoped = self.with_scene_without_film_options(
            &project,
            scene_id,
            motionwright_domain::RendererKind::ManimCommunity,
        )?;
        let source = crate::manim_static_projection::prepare_saved_manim_static_source(
            &self.service,
            project_id,
            expected,
            scene_id,
            deliverable_id,
        )?;
        scoped
            .realize_prepared_manim_scene(
                project_id,
                expected,
                request_id,
                scene_id,
                PreparedManimRender {
                    start_command: "driver.manim-community.static.render.start",
                    plan_value: serde_json::to_value(&source.plan)
                        .map_err(|_| invalid("Static Manim source cannot be encoded"))?,
                    plan_sha256: source.closure.plan_sha256,
                    profile: source.plan.render_profile,
                    requires_camera_getters: false,
                    source_bundle: None,
                },
            )
            .await
    }

    /// Plain native text/font/style source with the original canonical identity,
    /// private Asset/CAS bytes and the same sealed Driver SDK job lifecycle.
    pub async fn realize_manim_editable_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
        deliverable_id: Uuid,
    ) -> NativeResult<Value> {
        validate_manim_request_id(request_id)?;
        manim_graph::require_non_graph_saved_selection(
            &self.service,
            project_id,
            expected,
            scene_id,
            deliverable_id,
        )?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Manim,
        )?;
        let scoped = self.with_scene_without_film_options(
            &project,
            scene_id,
            motionwright_domain::RendererKind::ManimCommunity,
        )?;
        let source = crate::manim_editable_projection::prepare_saved_manim_editable_source(
            &self.service,
            project_id,
            expected,
            scene_id,
            deliverable_id,
        )?;
        scoped.recheck_scene_boundary_before_dispatch(
            &project,
            "driver.manim-community.editable.render.start",
            &Value::Null,
        )?;
        crate::manim_editable_projection::stage_font_inputs(
            &self.client.connection().output_root,
            &source,
        )?;
        scoped
            .realize_prepared_manim_scene(
                project_id,
                expected,
                request_id,
                scene_id,
                PreparedManimRender {
                    start_command: "driver.manim-community.editable.render.start",
                    plan_value: serde_json::to_value(&source.plan)
                        .map_err(|_| invalid("Editable source cannot be encoded"))?,
                    plan_sha256: source.closure.plan_sha256,
                    profile: source.plan.render_profile,
                    requires_camera_getters: false,
                    source_bundle: None,
                },
            )
            .await
    }

    /// Open an exported closed source through the same admitted Driver Host.
    /// Inspection cannot grant execution; execute() retains the normal current
    /// project stamp, request ledger, provider generation and unknown outcome.
    pub async fn reopen_manim_editable_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
        deliverable_id: Uuid,
        input: crate::manim_editable_projection::OriginalEditableBundleInput,
    ) -> NativeResult<Value> {
        validate_manim_request_id(request_id)?;
        manim_graph::require_non_graph_saved_selection(
            &self.service,
            project_id,
            expected,
            scene_id,
            deliverable_id,
        )?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Manim,
        )?;
        let scoped = self.with_scene_without_film_options(
            &project,
            scene_id,
            motionwright_domain::RendererKind::ManimCommunity,
        )?;
        let source = crate::manim_editable_projection::prepare_saved_manim_editable_bundle_input(
            &self.service,
            project_id,
            expected,
            scene_id,
            deliverable_id,
            &self.client.connection().output_root,
            &input,
        )?;
        scoped
            .realize_prepared_manim_scene(
                project_id,
                expected,
                request_id,
                scene_id,
                PreparedManimRender {
                    start_command: "driver.manim-community.editable.render.start",
                    plan_value: serde_json::to_value(&source.plan)
                        .map_err(|_| invalid("Original editable plan cannot be encoded"))?,
                    plan_sha256: source.closure.plan_sha256,
                    profile: source.plan.render_profile,
                    requires_camera_getters: false,
                    source_bundle: Some(PreparedManimSourceInput::Editable(input)),
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
        manim_graph::require_non_graph_saved_selection(
            &self.service,
            project_id,
            expected,
            scene_id,
            deliverable_id,
        )?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Manim,
        )?;
        let scoped = self.with_scene_without_film_options(
            &project,
            scene_id,
            motionwright_domain::RendererKind::ManimCommunity,
        )?;
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
        scoped
            .realize_prepared_manim_scene(
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
                    source_bundle: None,
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
            source_bundle,
        } = prepared;

        let expected_profile = json!(profile);
        let mut start_args = json!({"plan": plan_value, "profile": profile});
        if let Some(input) = &source_bundle {
            start_args["source_bundle"] = input.start_value(start_command)?;
        }
        let started = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:start"),
                start_command,
                start_args,
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
        require_acknowledged_manim_observation(
            &started,
            &job_ref,
            provider_generation,
            &plan_sha256,
            &expected_profile,
            start_command,
            "start",
        )?;

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
                Err(error) if retryable_manim_status_error(&error) => {
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
                &plan_sha256,
                &expected_profile,
                start_command,
                "status",
            )?;
            match status_data.get("state").and_then(Value::as_str) {
                Some("succeeded") => break,
                Some("failed") => {
                    return Err(retained_manim_job_failure(
                        status_data,
                        false,
                        start_command,
                    ));
                }
                Some("unknown") => {
                    return Err(retained_manim_job_failure(status_data, true, start_command));
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
            &plan_sha256,
            &expected_profile,
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
        if start_command == "driver.manim-community.static.render.start" {
            let source_check = serde_json::from_value(plan_value.clone())
                .map_err(|_| invalid("Original static Manim plan cannot be reopened"))
                .and_then(|plan: motionwright_manim_profile::ManimScenePlanV2| {
                    crate::manim_static_projection::verify_rendered_manim_static_source(
                        &self.client.connection().output_root,
                        &relative_path,
                        &plan,
                        &artifact,
                    )
                });
            match source_check {
                Ok(source) => verified_artifact["static_source"] = source,
                Err(error) => {
                    verified_artifact["static_source_custody_error"] =
                        json!({"message": error.to_string()})
                }
            }
        }
        if start_command == "driver.manim-community.editable.render.start" {
            let check = serde_json::from_value(plan_value.clone())
                .map_err(|_| invalid("Original editable plan cannot be reopened"))
                .and_then(|plan: motionwright_manim_profile::editable_scene::Plan| {
                    crate::manim_editable_projection::verify_rendered_manim_editable_source(
                        &self.client.connection().output_root,
                        &relative_path,
                        &plan,
                        &artifact,
                    )
                });
            match check {
                Ok(source) => verified_artifact["editable_source"] = source,
                Err(error) => {
                    verified_artifact["editable_source_custody_error"] =
                        json!({"message":error.to_string()})
                }
            }
        }

        if start_command == manim_graph::START_COMMAND {
            let check = serde_json::from_value(plan_value.clone())
                .map_err(|_| invalid("Original Graph plan cannot be reopened"))
                .and_then(
                    |plan: motionwright_manim_profile::semantic_graph::ManimSemanticGraphPlan| {
                        manim_graph::verify_rendered_source(
                            &self.service,
                            &self.client.connection().output_root,
                            &relative_path,
                            &plan,
                            &artifact,
                        )
                    },
                );
            match check {
                Ok(source) => verified_artifact["graph_source"] = source,
                Err(error) => {
                    verified_artifact["graph_source_custody_error"] =
                        json!({"message": error.to_string()});
                }
            }
        }

        if let Some(input) = &source_bundle {
            let (key, check) = match input {
                PreparedManimSourceInput::Editable(input) => {
                    let check = serde_json::from_value(plan_value.clone())
                        .map_err(|_| invalid("Original editable input plan cannot be reopened"))
                        .and_then(|plan: motionwright_manim_profile::editable_scene::Plan| {
                            crate::manim_editable_projection::verify_rendered_manim_editable_bundle_input(
                                &self.client.connection().output_root,
                                &plan,
                                input,
                                &artifact,
                            )
                        });
                    ("editable_input_source", check)
                }
                PreparedManimSourceInput::Graph(input) => {
                    let check = serde_json::from_value(plan_value.clone())
                        .map_err(|_| invalid("Original Graph input plan cannot be reopened"))
                        .and_then(|plan: motionwright_manim_profile::semantic_graph::ManimSemanticGraphPlan| {
                            crate::manim_graph_projection::verify_rendered_manim_semantic_graph_bundle_input(
                                &self.client.connection().output_root,
                                &plan,
                                input,
                                &artifact,
                            )
                        });
                    ("graph_input_source", check)
                }
            };
            match check {
                Ok(observed) => verified_artifact[key] = observed,
                Err(error) => {
                    // Retain the native terminal even if input custody fails.
                    verified_artifact[format!("{key}_custody_error")] =
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
        self.execute_with_receipt_origin(project_id, expected, request_id, command, args, mutation)
            .await
            .map(ReceiptExecutionOutcome::into_legacy_value)
    }

    async fn execute_with_receipt_origin(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<ReceiptExecutionOutcome> {
        self.execute_with_receipt_origin_context(
            project_id,
            expected,
            request_id,
            (command, mutation),
            args,
            None,
        )
        .await
    }

    #[cfg(test)]
    async fn execute_with_receipt_context(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        operation: (&str, bool),
        args: Value,
        receipt_context: Option<audio_analysis::AudioReceiptContext>,
    ) -> NativeResult<Value> {
        self.execute_with_receipt_origin_context(
            project_id,
            expected,
            request_id,
            operation,
            args,
            receipt_context.map(ReceiptDispatchContext::Audio),
        )
        .await
        .map(ReceiptExecutionOutcome::into_legacy_value)
    }

    async fn execute_with_prepared_audio_context(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        operation: (&str, bool),
        args: Value,
        receipt_context: Option<(
            audio_analysis::AudioReceiptContext,
            audio_analysis::AudioStaging<'_>,
        )>,
    ) -> NativeResult<Value> {
        self.execute_with_receipt_origin_context(
            project_id,
            expected,
            request_id,
            operation,
            args,
            receipt_context
                .map(|(context, staging)| ReceiptDispatchContext::PreparedAudio(context, staging)),
        )
        .await
        .map(ReceiptExecutionOutcome::into_legacy_value)
    }

    // One dispatch/custody body for all production. Audio contributes only
    // closed private metadata to the same existing receipt payloads.
    async fn execute_with_receipt_origin_context(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        operation: (&str, bool),
        args: Value,
        dispatch_context: Option<ReceiptDispatchContext<'_>>,
    ) -> NativeResult<ReceiptExecutionOutcome> {
        let (command, mutation) = operation;
        let (receipt_context, media_binding, audio_staging) = match dispatch_context {
            #[cfg(test)]
            Some(ReceiptDispatchContext::Audio(context)) => (Some(context), None, None),
            Some(ReceiptDispatchContext::PreparedAudio(context, staging)) => {
                (Some(context), None, Some(staging))
            }
            Some(ReceiptDispatchContext::Bound(binding)) => (None, Some(binding), None),
            None => (None, None, None),
        };
        let media_mutation = if let Some(binding) = media_binding {
            binding.check_connection(&self.client)?;
            binding.mutation_for(command)?
        } else {
            false
        };
        let mutation = mutation
            || media_mutation
            || WORKFLOW_MUTATING_COMMANDS.contains(&command)
            || HYPERFRAMES_MUTATING_COMMANDS.contains(&command)
            || BLENDER_PRODUCT_MUTATING_COMMANDS.contains(&command)
            || command == manim_graph::START_COMMAND;
        if receipt_context.is_some()
            && (command != "driver.audio-analysis.artifact.measure" || mutation)
        {
            return Err(invalid(
                "Private audio receipt context requires the read-only meter",
            ));
        }
        let receipt_context = receipt_context.map(|context| context.payload());
        let media_context =
            media_binding.map(|binding| (binding.receipt_key(), binding.receipt_context()));
        let with_context = |mut payload: Value| {
            if let Some((key, context)) = &media_context {
                payload[*key] = context.clone();
            }
            if let Some(context) = &receipt_context {
                payload["audio_reopen"] = context.clone();
            }
            payload
        };
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
        let digest = if media_context.is_some() {
            request_digest(command, &with_context(json!({"args": &args})))?
        } else {
            request_digest(command, &args)?
        };

        if let Some(prior) = self
            .service
            .latest_production_receipt(project_id, request_id)
            .map_err(storage_error)?
        {
            if prior.request_sha256 != digest || prior.command != command {
                let error = Error::new(
                    ErrorCode::Conflict,
                    "Production request id was reused with different input",
                );
                // A fresh canonical binding changes this private digest. It
                // cannot settle an earlier mutation whose terminal is unknown.
                let unresolved_bound_effect = media_binding.is_some()
                    && (prior.stage == "outcome_unknown"
                        || (prior.stage == "dispatching"
                            && prior.payload.get("mutation").and_then(Value::as_bool)
                                == Some(true)));
                return Err(if unresolved_bound_effect {
                    error.uncertain()
                } else {
                    error
                });
            }
            // A completed request remains bound to its original authoring stamp.
            // A fresh expected reference cannot turn an older result into current
            // evidence; unresolved receipts retain their existing uncertainty path.
            if prior.stage == "completed"
                && (prior.generation != project.generation || prior.revision != project.revision)
            {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Production request id is bound to another project revision",
                ));
            }
            if prior.stage == "completed"
                && let Some(recorded) = prior.payload.get("audio_reopen")
                && receipt_context
                    .as_ref()
                    .is_some_and(|context| context != recorded)
            {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Production request has different original audio selector metadata",
                ));
            }
            if prior.stage == "completed"
                && let Some(result) = prior.payload.get("result")
            {
                manim_graph::validate_dispatch_source(&project, command, &args)?;
                manim_graph::recheck_before_dispatch(&self.service, &project, command, &args)?;
                return Ok(ReceiptExecutionOutcome {
                    origin: ReceiptExecutionOrigin::ReceiptReplay(json!({
                        "replayed": true,
                        "result": result,
                        "receipt": prior
                    })),
                });
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

        // Completed/UNKNOWN receipts above retain their original precedence.
        crate::native_scene_boundary::guard_new_effect(
            &project,
            command,
            self.scene_boundary_scope.as_deref(),
            args.get("profile").and_then(Value::as_str),
        )?;
        manim_graph::validate_dispatch_source(&project, command, &args)?;

        #[cfg(test)]
        if let Some(before_dispatch_admission) = &self.before_dispatch_admission {
            before_dispatch_admission();
        }

        manim_graph::recheck_before_dispatch(&self.service, &project, command, &args)?;
        self.recheck_scene_boundary_before_dispatch(&project, command, &args)?;

        // Capacity is claimed before the durable dispatch intent and native
        // effect. Every pre-terminal abort releases this reservation by Drop.
        let mut custody =
            self.receipt_custody
                .reserve(mutation, request_id, command, job_ref.as_deref())?;

        let intent = ProductionReceiptInput {
            project_id,
            generation: project.generation,
            revision: project.revision,
            request_id: request_id.into(),
            request_sha256: digest.clone(),
            command: command.into(),
            stage: "dispatching".into(),
            payload: with_context(json!({
                "mutation": mutation,
                "connection": self.client.connection().identity()?,
                "job_ref": job_ref.clone()
            })),
        };
        let budget = match (&self.active_hybrid_budget, &self.active_motion_budget) {
            (Some(_), Some(_)) => {
                return Err(invalid(
                    "Native dispatch cannot attach two accounting roots",
                ));
            }
            (Some(root), None) => {
                Some(root.dispatch_budget(self, &project, request_id, command, &args, mutation)?)
            }
            (None, Some(root)) => Some((root.root_id, root.units_for(command, &args, mutation)?)),
            (None, None) => None,
        };
        if let Some((root_id, units)) = budget {
            // One SQLite IMMEDIATE transaction writes both the debit and
            // original dispatching receipt before ANY Driver SDK invocation.
            // Failed, interrupted and UNKNOWN attempts do not refund credit.
            match self
                .service
                .admit_root_operation_dispatch(RootDispatchBudgetInput {
                    root_id,
                    receipt: intent,
                    units,
                })
                .map_err(storage_error)?
            {
                RootDispatchBudgetOutcome::Admitted(_) => {}
                RootDispatchBudgetOutcome::AlreadyCompleted(prior) => {
                    let previous_result =
                        prior.payload.get("result").cloned().ok_or_else(|| {
                            backend("Stored native replay has no original result")
                        })?;
                    return Ok(ReceiptExecutionOutcome {
                        origin: ReceiptExecutionOrigin::ReceiptReplay(json!({
                            "replayed": true,
                            "result": previous_result,
                            "receipt": prior
                        })),
                    });
                }
            }
        } else {
            // Existing unscoped read/legacy paths remain subject to the
            // SQLite root trigger. Once a project adopts a root, no uncharged
            // alternate producer can append a fresh dispatching receipt.
            self.service
                .append_production_receipt(intent)
                .map_err(storage_error)?;
        }

        let dispatched = async {
            if let Some(hybrid) = &self.active_hybrid_budget {
                // The original WAV is staged only by its closed producer,
                // after this request's immutable debit and intent exist.
                hybrid.stage_after_admission(self, request_id, command)?;
            }
            if let Some(staging) = audio_staging {
                // Admission above durably charged the original root before
                // either the bounded input copy or any SDK/provider effect.
                // A staging failure follows the same terminal custody below;
                // no credit is refunded and no alternate root is created.
                staging.stage_after_admission(&self.client.connection().output_root)?;
            }
            match media_binding {
                Some(binding) => {
                    self.client
                        .execute_bound_media_transport(binding, command, args, mutation)
                        .await
                }
                None => self.client.execute(command, args, mutation).await,
            }
        }
        .await;
        match dispatched {
            Ok(result) => {
                #[cfg(all(test, unix))]
                let observed_sdk_value = result.value.clone();
                #[cfg(all(test, unix))]
                let observed_broker_request_id = json!(&result.request_id);
                // Hold any pure bound qualification error without returning.
                // Every result, including rejected metadata, reaches the same
                // original-result custody and append path before propagation.
                let bound_error = media_binding
                    .and_then(|binding| binding.qualify_result(&result, command, mutation).err());
                let stage = match &bound_error {
                    Some(error) if error.outcome_known => "failed_known",
                    Some(_) => "outcome_unknown",
                    None => "completed",
                };
                let mut original_payload = with_context(json!({
                    "result": result.value,
                    "broker_request_id": result.request_id,
                    "job_ref": job_ref
                }));
                if let Some(binding) = media_binding {
                    original_payload["mutation"] = json!(mutation);
                    let historical = bound_error.as_ref().is_some_and(|error| {
                        error.code == ErrorCode::StaleReference && error.outcome_known
                    });
                    original_payload[binding.qualification_key()] = json!(if historical {
                        "historical"
                    } else if bound_error.is_some() {
                        "rejected"
                    } else {
                        "admitted"
                    });
                    if historical {
                        original_payload["native_completion"] = json!("confirmed");
                        original_payload["rejected_scope"] = json!("current_admission");
                    }
                    if let Some(error) = &bound_error {
                        original_payload["error"] = json!(error);
                    }
                }
                // The original JSON structure is unchanged in this payload.
                // Optional transport custody separately retains binary streams.
                custody.observe(ProductionReceiptInput {
                    project_id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: request_id.into(),
                    request_sha256: digest,
                    command: command.into(),
                    stage: stage.into(),
                    payload: original_payload,
                });
                match self
                    .service
                    .append_production_receipt(custody.attempted_receipt())
                {
                    Ok(receipt) => {
                        custody.confirmed();
                        if let Some(error) = bound_error {
                            return Err(error);
                        }
                        Ok(ReceiptExecutionOutcome {
                            origin: ReceiptExecutionOrigin::FreshAuthenticated(Box::new(
                                AuthenticatedSdkResult {
                                    completed_receipt: receipt,
                                    #[cfg(all(test, unix))]
                                    command: command.to_owned(),
                                    mutation,
                                    #[cfg(all(test, unix))]
                                    caller_request_id: request_id.to_owned(),
                                    #[cfg(all(test, unix))]
                                    project_id,
                                    #[cfg(all(test, unix))]
                                    expected_stamp: json!(expected),
                                    #[cfg(all(test, unix))]
                                    result_value: observed_sdk_value,
                                    #[cfg(all(test, unix))]
                                    broker_request_id: observed_broker_request_id,
                                },
                            )),
                        })
                    }
                    Err(storage_error) => {
                        custody.failed(storage_error);
                        if let Some(error) = bound_error {
                            return Err(error);
                        }
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
                    payload: with_context(json!({
                        "error": &error,
                        "mutation": mutation,
                        "job_ref": job_ref
                    })),
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

#[cfg(test)]
mod native_motion_root_budget_tests {
    use super::*;

    fn scope() -> MotionRootBudgetScope {
        MotionRootBudgetScope {
            root_id: Uuid::nil(),
            max_render_frames: 24,
            max_frame_bytes: 1024,
            mlt_route: None,
            mlt: None,
            audio_analysis: false,
        }
    }

    #[test]
    fn read_only_children_consume_one_durable_attempt_unit() {
        let units = scope()
            .units_for(
                "driver.motion-canvas.render.status",
                &json!({"job_ref": "scoped-fixture"}),
                false,
            )
            .unwrap();
        assert_eq!(units.attempts, 1);
        assert_eq!(units.model_calls, 0);
        assert_eq!(units.render_frames, 0);
        assert_eq!(units.output_bytes, 0);
    }

    #[test]
    fn render_start_reserves_original_frames_and_bytes() {
        let units = scope()
            .units_for(
                "driver.motion-canvas.render.start",
                &json!({"profile":{"first_frame":0,"end_frame_exclusive":12}}),
                true,
            )
            .unwrap();
        assert_eq!(units.attempts, 1);
        assert_eq!(units.render_frames, 12);
        assert_eq!(units.output_bytes, 12 * 1024);
        assert_eq!(units.model_calls, 0);
    }

    #[test]
    fn font_import_reserves_owner_verified_source_ceiling() {
        let units = scope()
            .units_for("driver.motion-canvas.asset.import", &json!({}), true)
            .unwrap();
        assert_eq!(units.attempts, 1);
        assert_eq!(units.output_bytes, MAX_BRAND_FONT_BYTES);
        assert_eq!(units.render_frames, 0);
    }

    #[test]
    fn project_source_mutations_consume_reserved_output_bytes() {
        for command in [
            "driver.motion-canvas.project.create",
            "driver.motion-canvas.composition.apply",
            "driver.motion-canvas.render.cancel",
        ] {
            let units = scope().units_for(command, &json!({}), true).unwrap();
            assert_eq!(units.attempts, 1);
            assert_eq!(units.output_bytes, MOTION_ROOT_FRAME_RESERVE_MIN_BYTES);
            assert_eq!(units.render_frames, 0);
        }
    }

    #[test]
    fn over_budget_or_undocumented_effect_is_denied_before_native_dispatch() {
        assert!(
            scope()
                .units_for(
                    "driver.motion-canvas.render.start",
                    &json!({"profile":{"first_frame":0,"end_frame_exclusive":25}}),
                    true,
                )
                .is_err()
        );
        assert!(
            scope()
                .units_for(
                    "driver.motion-canvas.render.start",
                    &json!({"profile":{"first_frame":0,"end_frame_exclusive":4}}),
                    false,
                )
                .is_err()
        );
        assert!(
            scope()
                .units_for("driver.blender.object.create", &json!({}), true)
                .is_err()
        );
        assert!(
            scope()
                .units_for("driver.motion-canvas.scene.unknown", &json!({}), true)
                .is_err()
        );
    }
}

// Closed internal dispatch metadata; it cannot be supplied by stored caller JSON.
enum ReceiptDispatchContext<'a> {
    #[cfg(test)]
    Audio(audio_analysis::AudioReceiptContext),
    PreparedAudio(
        audio_analysis::AudioReceiptContext,
        audio_analysis::AudioStaging<'a>,
    ),
    Bound(&'a provider_binding::BoundMediaContext),
}

// Private dispatch provenance. Stored JSON cannot construct an observed result.
pub(super) struct AuthenticatedSdkResult {
    completed_receipt: ProductionReceipt,
    #[cfg(all(test, unix))]
    command: String,
    mutation: bool,
    #[cfg(all(test, unix))]
    caller_request_id: String,
    #[cfg(all(test, unix))]
    project_id: Uuid,
    #[cfg(all(test, unix))]
    expected_stamp: Value,
    #[cfg(all(test, unix))]
    result_value: Value,
    #[cfg(all(test, unix))]
    broker_request_id: Value,
}

impl AuthenticatedSdkResult {
    fn legacy_value(&self) -> Value {
        json!({
            "replayed": false,
            "result": &self.completed_receipt.payload["result"],
            "receipt": &self.completed_receipt,
        })
    }
}

#[cfg(all(test, unix))]
impl AuthenticatedSdkResult {
    fn command(&self) -> &str {
        &self.command
    }
    fn is_mutation(&self) -> bool {
        self.mutation
    }
    fn caller_request_id(&self) -> &str {
        &self.caller_request_id
    }
    fn project_id(&self) -> Uuid {
        self.project_id
    }
    fn expected_stamp(&self) -> &Value {
        &self.expected_stamp
    }
    fn result_value(&self) -> &Value {
        &self.result_value
    }
    fn broker_request_id(&self) -> &Value {
        &self.broker_request_id
    }
    fn completed_receipt(&self) -> &ProductionReceipt {
        &self.completed_receipt
    }
}

enum ReceiptExecutionOrigin {
    ReceiptReplay(Value),
    FreshAuthenticated(Box<AuthenticatedSdkResult>),
}

struct ReceiptExecutionOutcome {
    origin: ReceiptExecutionOrigin,
}

impl ReceiptExecutionOutcome {
    fn into_legacy_value(self) -> Value {
        match self.origin {
            ReceiptExecutionOrigin::ReceiptReplay(value) => value,
            ReceiptExecutionOrigin::FreshAuthenticated(observed) => observed.legacy_value(),
        }
    }

    #[cfg(all(test, unix))]
    fn legacy_value(&self) -> Value {
        match &self.origin {
            ReceiptExecutionOrigin::ReceiptReplay(value) => value.clone(),
            ReceiptExecutionOrigin::FreshAuthenticated(observed) => observed.legacy_value(),
        }
    }

    fn into_authenticated_result(self) -> Option<Box<AuthenticatedSdkResult>> {
        match self.origin {
            ReceiptExecutionOrigin::ReceiptReplay(_) => None,
            ReceiptExecutionOrigin::FreshAuthenticated(observed) => Some(observed),
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
    include!("production/manim_graph/coordinator_tests.rs");
    use super::*;
    #[cfg(unix)]
    mod root_budget_productive_tests {
        use super::*;
        use std::sync::atomic::Ordering;
        include!("production/root_budget_productive_tests.rs");
    }
    #[cfg(unix)]
    use motionwright_domain::{BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle};
    #[cfg(unix)]
    use motionwright_storage::Store;
    #[cfg(unix)]
    use semwright_media_time::Rate;
    #[cfg(unix)]
    use semwright_motion_authoring::{Archetype, NarrativeRole};
    #[cfg(unix)]
    use semwright_native_sdk::types::{Execution, InvocationProvenance};
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    #[cfg(unix)]
    use std::{collections::BTreeSet, fs::File};

    #[cfg(unix)]
    #[tokio::test]
    async fn blender_product_selected_commands_reach_the_existing_driver_transport() {
        // Transport selection only: the fixture executes no Broker or Blender.
        for command in [
            "driver.blender.introspect.summary",
            "driver.blender.composition.measure",
            "driver.blender.animation.keyframe.insert",
            "driver.blender.scene.marker.add",
            "driver.blender.node.add",
            "driver.blender.node.remove",
            "driver.blender.node.link",
        ] {
            let temp = tempfile::tempdir().unwrap();
            // Build provenance from an independently existing Blender command,
            // so the positive oracle does not consult the new selection.
            let mut reply = transport_reply("driver.blender.scene.inspect");
            reply.command = command.into();
            let output = serde_json::to_string(&reply).unwrap();
            let (connection, counter) =
                fake_transport_connection(&temp, "project:test".into(), &output, "", 0);
            let client = ProductionClient::new(connection).unwrap();
            let result = client
                .execute(command, json!({"transport_fixture_only": true}), false)
                .await
                .unwrap();
            assert_eq!(result.request_id, "transport-fixture-original", "{command}");
            assert_eq!(result.value["data"], json!({"changed": true}), "{command}");
            assert_eq!(result.value["provenance_checked"], true, "{command}");
            assert_eq!(
                result.value["execution"]["provenance"]["provider"], "driver:blender",
                "{command}"
            );
            assert_eq!(fs::read(&counter).unwrap(), b"invoked\n", "{command}");
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn blender_product_selection_keeps_unselected_neighbors_before_spawn() {
        let temp = tempfile::tempdir().unwrap();
        let output =
            serde_json::to_string(&transport_reply("driver.blender.scene.inspect")).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, "project:test".into(), &output, "", 0);
        let client = ProductionClient::new(connection).unwrap();
        for command in [
            "driver.blender.python.exec",
            "driver.blender.operator.invoke",
            "driver.blender.introspect.summary.extra",
            "driver.blender.animation.keyframe.delete",
            "driver.blender.scene.marker.remove",
            "driver.blender.node.unlink",
            "driver.blender.composition.repair.apply",
        ] {
            let error = client.execute(command, json!({}), true).await.unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported, "{command}");
            assert!(error.outcome_known, "{command}");
            assert!(!counter.exists(), "{command} must not launch the CLI");
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn blender_product_effects_preserve_unknown_transport_despite_false_hint() {
        for (command, effectful) in [
            ("driver.blender.introspect.summary", false),
            ("driver.blender.composition.measure", true),
            ("driver.blender.animation.keyframe.insert", true),
            ("driver.blender.scene.marker.add", true),
            ("driver.blender.node.add", true),
            ("driver.blender.node.remove", true),
            ("driver.blender.node.link", true),
        ] {
            let temp = tempfile::tempdir().unwrap();
            let (connection, counter) =
                fake_transport_connection(&temp, "project:test".into(), "{broken", "", 0);
            let client = ProductionClient::new(connection).unwrap();
            let error = client
                .execute(command, json!({"transport_fixture_only": true}), false)
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::BackendFailed, "{command}");
            assert_eq!(error.outcome_known, !effectful, "{command}");
            assert_eq!(fs::read(&counter).unwrap(), b"invoked\n", "{command}");
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn blender_product_unknown_receipts_survive_restart_without_redispatch() {
        for command in [
            "driver.blender.composition.measure",
            "driver.blender.animation.keyframe.insert",
            "driver.blender.scene.marker.add",
            "driver.blender.node.add",
            "driver.blender.node.remove",
            "driver.blender.node.link",
        ] {
            let (service, project, temp) = fixture_service();
            let (connection, counter) =
                fake_transport_connection(&temp, project.resource_key(), "{broken", "", 0);
            let coordinator =
                ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
            let args = json!({"transport_fixture_only": true});
            let request_id = "blender-product-original-unknown";
            let error = coordinator
                .execute(
                    project.id,
                    &project.stamp(),
                    request_id,
                    command,
                    args.clone(),
                    false,
                )
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::BackendFailed, "{command}");
            assert!(!error.outcome_known, "{command}");
            assert_eq!(fs::read(&counter).unwrap(), b"invoked\n", "{command}");
            assert_eq!(service.project(project.id).unwrap(), project, "{command}");
            let receipts = service.production_receipts(project.id, 10).unwrap();
            assert_eq!(receipts.len(), 2, "{command}");
            let dispatch = receipts
                .iter()
                .find(|receipt| receipt.stage == "dispatching")
                .unwrap();
            let terminal = receipts
                .iter()
                .find(|receipt| receipt.stage == "outcome_unknown")
                .unwrap();
            assert_eq!(dispatch.payload["mutation"], true, "{command}");
            assert_eq!(terminal.payload["mutation"], true, "{command}");
            assert_eq!(
                terminal.payload["error"],
                serde_json::to_value(&error).unwrap(),
                "{command}"
            );
            assert!(receipts.iter().all(|receipt| {
                receipt.project_id == project.id
                    && receipt.generation == project.generation
                    && receipt.revision == project.revision
                    && receipt.request_id == request_id
                    && receipt.command == command
            }));
            drop(coordinator);
            drop(service);

            let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
            let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
            let blocked = recovered
                .execute(
                    project.id,
                    &project.stamp(),
                    request_id,
                    command,
                    args,
                    false,
                )
                .await
                .unwrap_err();
            assert_eq!(blocked.code, ErrorCode::Conflict, "{command}");
            assert!(!blocked.outcome_known, "{command}");
            assert_eq!(
                recovered
                    .client
                    .execute_calls
                    .load(std::sync::atomic::Ordering::SeqCst),
                0,
                "{command} must stop before reentering the transport"
            );
            assert_eq!(fs::read(&counter).unwrap(), b"invoked\n", "{command}");
            assert_eq!(reopened.project(project.id).unwrap(), project, "{command}");
            assert_eq!(
                reopened.production_receipts(project.id, 10).unwrap(),
                receipts,
                "{command}"
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn blender_product_added_commands_reject_foreign_or_missing_provenance() {
        for (command, effectful) in [
            ("driver.blender.introspect.summary", false),
            ("driver.blender.composition.measure", true),
        ] {
            for invalid_field in ["provider", "source", "generation", "descriptor"] {
                let temp = tempfile::tempdir().unwrap();
                let mut reply = transport_reply("driver.blender.scene.inspect");
                reply.command = command.into();
                let provenance = reply.execution.provenance.as_mut().unwrap();
                match invalid_field {
                    "provider" => provenance.provider = "semwright-core".into(),
                    "source" => provenance.source = SourceKind::Builtin,
                    "generation" => provenance.provider_generation = None,
                    "descriptor" => provenance.descriptor_sha256 = "invalid".into(),
                    _ => unreachable!(),
                }
                let output = serde_json::to_string(&reply).unwrap();
                let (connection, counter) =
                    fake_transport_connection(&temp, "project:test".into(), &output, "", 0);
                let client = ProductionClient::new(connection).unwrap();
                let error = client.execute(command, json!({}), false).await.unwrap_err();
                assert_eq!(
                    error.code,
                    ErrorCode::BackendFailed,
                    "{command}/{invalid_field}"
                );
                assert_eq!(error.outcome_known, !effectful, "{command}/{invalid_field}");
                assert_eq!(
                    fs::read(&counter).unwrap(),
                    b"invoked\n",
                    "{command}/{invalid_field}"
                );
            }
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn blender_product_typed_policy_denial_stays_known_without_redispatch() {
        // Destructive remains the SDK classification of node.remove. The
        // transport floor must not erase a known typed policy denial.
        let command = "driver.blender.node.remove";
        let expected = Error::new(ErrorCode::PolicyDenied, "synthetic typed policy denial");
        let expected_json = serde_json::to_value(&expected).unwrap();
        let mut reply = transport_reply("driver.blender.scene.inspect");
        reply.command = command.into();
        reply.ok = false;
        reply.execution.policy_decision = "deny".into();
        reply.data = None;
        reply.error = Some(expected);
        let output = serde_json::to_string(&reply).unwrap();
        let (service, project, temp) = fixture_service();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 1);
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let request_id = "blender-product-policy-denied";
        let args = json!({"transport_fixture_only": true});
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args.clone(),
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(serde_json::to_value(&error).unwrap(), expected_json);
        assert_eq!(error.code, ErrorCode::PolicyDenied);
        assert!(error.outcome_known);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        let receipts = service.production_receipts(project.id, 10).unwrap();
        assert_eq!(receipts.len(), 2);
        assert_eq!(
            receipts
                .iter()
                .find(|receipt| receipt.stage == "dispatching")
                .unwrap()
                .payload["mutation"],
            true
        );
        let terminal = receipts
            .iter()
            .find(|receipt| receipt.stage == "failed_known")
            .unwrap();
        assert_eq!(terminal.payload["mutation"], true);
        assert_eq!(terminal.payload["error"], expected_json);
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(coordinator);
        drop(service);

        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let blocked = recovered
            .execute(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args,
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(blocked.code, ErrorCode::Conflict);
        assert!(blocked.outcome_known);
        assert_eq!(
            recovered
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(reopened.project(project.id).unwrap(), project);
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            receipts
        );
    }

    #[cfg(unix)]
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

        let held_custody = saturate_native_boundary_test_custody(&coordinator);
        let custody_before = coordinator.receipt_custody.snapshot();
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
        assert_eq!(coordinator.receipt_custody.snapshot(), custody_before);
        drop(held_custody);
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

    #[cfg(unix)]
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
    async fn fractional_rate_conversion_is_refused_before_dispatch_and_exact_rate_runs() {
        use std::sync::atomic::Ordering;

        let rate = Rate::new(30_000, 1_001).unwrap();
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let scene_id = project.scenes[0].id;
        let mut profile = project.deliverables[0].clone();
        let deliverable_id = profile.id;
        profile.frame_rate = motionwright_domain::RationalTime::new(30_000, 1_001).unwrap();
        let project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "rate-fixture-profile",
                &Change::UpsertDeliverable { profile },
            )
            .unwrap()
            .project;
        let project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "rate-fixture-duration",
                &Change::SetSceneDuration {
                    scene_id,
                    duration: rate.at(60).unwrap(),
                },
            )
            .unwrap()
            .project;
        let original = project.clone();
        let expected = RevisionStamp::from(&project);
        let connection = fake_render_connection(&temp, project.resource_key());
        let output_root = connection.output_root.clone();
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let mut options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: vec![crate::film::SceneFilmIntent {
                scene_id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            }],
        };

        // This exercises the actual application caller and Store with a
        // fractional saved rate and a different, valid integral output rate.
        let error = coordinator
            .render_motion_canvas_segments(
                project.id,
                &expected,
                "rate-conversion-refused",
                deliverable_id,
                &options,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.outcome_known);
        assert!(error.message.contains("saved_deliverable_rate=30000/1001"));
        assert!(error.message.contains("requested_output_rate=30/1"));
        assert!(
            error
                .message
                .contains("policy=preserve-saved-frame-rate-v1")
        );
        assert!(
            error
                .message
                .contains("no rounding or retiming was applied")
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert!(coordinator.receipts(project.id, 100).unwrap().is_empty());
        assert!(fs::read_dir(&output_root).unwrap().next().is_none());
        assert_eq!(service.project(project.id).unwrap(), original);

        // The positive uses the same caller and transport fixture, with an
        // exactly representable duration. It is coordinator G1, not a render.
        options.frame_rate = rate;
        let segments =
            crate::film::build_motion_canvas_segments(&project, deliverable_id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].frame_count, 60);
        assert_eq!(
            segments[0].global_start,
            motionwright_domain::RationalTime::ZERO
        );
        assert_eq!(segments[0].duration, rate.at(60).unwrap());
        assert_eq!(
            segments[0]
                .duration
                .checked_sub(rate.at(60).unwrap())
                .unwrap(),
            motionwright_domain::RationalTime::ZERO,
        );
        let rendered = coordinator
            .render_motion_canvas_segments(
                project.id,
                &expected,
                "rate-identity-exact",
                deliverable_id,
                &options,
            )
            .await
            .unwrap();
        assert_eq!(rendered.frame_rate, rate);
        assert_eq!(rendered.segments.len(), 1);
        assert_eq!(rendered.segments[0].frame_count, 60);
        assert_eq!(rendered.segments[0].artifact["frame_count"], 60);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 6);
        let receipts = coordinator.receipts(project.id, 100).unwrap();
        assert_eq!(receipts.len(), 12);
        assert!(
            receipts
                .iter()
                .all(|receipt| receipt.request_id.starts_with("rate-identity-exact:"))
        );
        assert_eq!(service.project(project.id).unwrap(), original);
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
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let project = persist_native_boundary_test_draft(&service, project);
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
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
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
            "driver.manim-community.static.render.start",
            "driver.manim-community.editable.render.start",
            "driver.manim-community.graph.render.start",
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
    fn manim_original_host_failures_preserve_typed_certainty_and_start() {
        for start_command in [
            "driver.manim-community.render.start",
            "driver.manim-community.camera.render.start",
            "driver.manim-community.static.render.start",
            "driver.manim-community.editable.render.start",
            "driver.manim-community.graph.render.start",
        ] {
            for (original, unknown_state) in [
                (
                    Error::new(ErrorCode::PolicyDenied, "original host denial"),
                    false,
                ),
                (
                    Error::new(ErrorCode::Unavailable, "original host observation lost")
                        .uncertain(),
                    true,
                ),
                (
                    Error::new(ErrorCode::Unavailable, "inconsistent failed view").uncertain(),
                    false,
                ),
                (
                    Error::new(ErrorCode::PolicyDenied, "inconsistent unknown view"),
                    true,
                ),
            ] {
                let expected = if unknown_state || !original.outcome_known {
                    original
                        .clone()
                        .uncertain()
                        .recipe_progress(1, start_command)
                } else {
                    original.clone()
                };
                let retained = retained_manim_job_failure(
                    &json!({"original_error": original}),
                    unknown_state,
                    start_command,
                );
                assert_eq!(
                    serde_json::to_value(retained).unwrap(),
                    serde_json::to_value(expected).unwrap()
                );
            }
            for view in [json!({}), json!({"original_error": {"malformed": true}})] {
                let unknown = retained_manim_job_failure(&view, true, start_command);
                assert!(!unknown.outcome_known);
                assert_eq!(unknown.code, ErrorCode::BackendFailed);
            }
        }
    }

    #[test]
    fn manim_status_retry_requires_an_ordinary_known_read_timeout() {
        let timeout = Error::new(ErrorCode::Timeout, "ordinary status read timeout");
        assert!(retryable_manim_status_error(&timeout));
        assert!(!retryable_manim_status_error(&timeout.uncertain()));
        for code in [
            ErrorCode::Unavailable,
            ErrorCode::StaleReference,
            ErrorCode::BackendFailed,
            ErrorCode::PolicyDenied,
        ] {
            let error = Error::new(code, "status read failed");
            assert!(!retryable_manim_status_error(&error));
            assert!(!retryable_manim_status_error(&error.uncertain()));
        }
    }

    #[test]
    fn manim_observations_require_original_job_and_provider_generation() {
        let command = "driver.manim-community.camera.render.start";
        let job = "private-native-original";
        let plan_sha256 = "ab".repeat(32);
        let profile = json!({"width": 640, "height": 360, "frame_rate": 30});
        let correct = json!({
            "result": {
                "data": {
                    "job_ref": job, "state": "rendering",
                    "plan_sha256": plan_sha256, "profile": profile
                },
                "execution": {"provenance": {"provider_generation": 17}}
            }
        });
        for kind in ["start", "status", "result"] {
            let mut correct = correct.clone();
            correct["result"]["data"]["state"] = json!(if kind == "start" {
                "rendering"
            } else {
                "succeeded"
            });
            assert!(
                require_acknowledged_manim_observation(
                    &correct,
                    job,
                    17,
                    &plan_sha256,
                    &profile,
                    command,
                    kind
                )
                .is_ok()
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
                ("/result/data/plan_sha256", json!("cd".repeat(32))),
                ("/result/data/plan_sha256", Value::Null),
                ("/result/data/profile/width", json!(1280)),
                ("/result/data/profile/height", json!(720)),
                ("/result/data/profile/frame_rate", json!(24)),
                ("/result/data/profile", Value::Null),
            ] {
                let mut wrong = correct.clone();
                *wrong.pointer_mut(pointer).unwrap() = replacement;
                let error = require_acknowledged_manim_observation(
                    &wrong,
                    job,
                    17,
                    &plan_sha256,
                    &profile,
                    command,
                    kind,
                )
                .unwrap_err();
                assert_eq!(error.code, ErrorCode::BackendFailed);
                assert!(!error.outcome_known);
                assert!(!error.message.contains("private-native-"));
            }
            for field in ["plan_sha256", "profile"] {
                let mut missing = correct.clone();
                missing["result"]["data"]
                    .as_object_mut()
                    .unwrap()
                    .remove(field);
                let error = require_acknowledged_manim_observation(
                    &missing,
                    job,
                    17,
                    &plan_sha256,
                    &profile,
                    command,
                    kind,
                )
                .unwrap_err();
                assert!(!error.outcome_known);
            }
        }
        for state in [
            json!("succeeded"),
            json!("unknown"),
            json!("queued"),
            Value::Null,
        ] {
            let mut wrong_start = correct.clone();
            wrong_start["result"]["data"]["state"] = state;
            let error = require_acknowledged_manim_observation(
                &wrong_start,
                job,
                17,
                &plan_sha256,
                &profile,
                command,
                "start",
            )
            .unwrap_err();
            assert!(!error.outcome_known);
        }
    }

    #[cfg(unix)]
    fn manim_fixture_job_data(kind: &str) -> Value {
        assert!(["start", "status", "result"].contains(&kind));
        let mut data = json!({
            "job_ref": "job-native-1",
            "state": if kind == "start" { "rendering" } else { "succeeded" },
            "source_file": "original.py",
            "plan_sha256": "ab".repeat(32),
            "profile": {"width": 640, "height": 360, "frame_rate": 30},
            "artifact": null,
            "diagnostic": null,
            "original_error": null,
        });
        if kind != "start" {
            data["artifact"] = json!({"directory":"render-native-1","frame_count":60});
            if kind == "result" {
                data["artifact"]["manifest_sha256"] = json!("bb".repeat(32));
            }
        }
        data
    }

    #[cfg(unix)]
    fn manim_fixture_data_line(kind: &str) -> String {
        format!(
            "data='{}'",
            serde_json::to_string(&manim_fixture_job_data(kind)).unwrap()
        )
    }

    #[cfg(unix)]
    fn fake_manim_render_connection(
        temp: &tempfile::TempDir,
        resource: String,
    ) -> (ProductionConnection, PathBuf) {
        let mut connection = fake_render_connection(temp, resource);
        let mut script = fs::read_to_string(&connection.executable)
            .unwrap()
            .replace("driver.motion-canvas", "driver.manim-community")
            .replace("driver:motion-canvas", "driver:manim-community")
            .replace(
                "driver.manim-community.render.start)",
                "driver.manim-community.render.start|driver.manim-community.camera.render.start|driver.manim-community.static.render.start|driver.manim-community.editable.render.start)",
            )
            .replacen(
                "command=\"$7\"",
                "command=\"$7\"\nprintf '%s\\n' \"$command\" >> \"$0.calls\"",
                1,
            );
        for (original, kind) in [
            (
                r#"data='{"job_ref":"job-native-1","state":"queued"}'"#,
                "start",
            ),
            (
                r#"data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60}}'"#,
                "status",
            ),
            (
                r#"data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60,"manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'"#,
                "result",
            ),
        ] {
            assert_eq!(script.matches(original).count(), 1, "fixture source drift");
            script = script.replace(original, &manim_fixture_data_line(kind));
        }
        fs::write(&connection.executable, script).unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
        let mut calls_path = connection.executable.as_os_str().to_os_string();
        calls_path.push(".calls");
        (connection, PathBuf::from(calls_path))
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
                let (mut connection, calls_path) =
                    fake_manim_render_connection(&temp, project.resource_key());
                let script = fs::read_to_string(&connection.executable).unwrap();
                let status_line = manim_fixture_data_line("status");
                let result_line = manim_fixture_data_line("result");
                let is_result = case.ends_with("_result");
                let line = if is_result {
                    result_line.as_str()
                } else {
                    status_line.as_str()
                };
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
                        data: (!is_error).then(|| {
                            json!({
                                "job_ref":"job-native-1", "state":"succeeded",
                                "plan_sha256":"ab".repeat(32),
                                "profile":{"width":640,"height":360,"frame_rate":30}
                            })
                        }),
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
                                source_bundle: None,
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
                let calls = fs::read_to_string(calls_path).unwrap();
                assert_eq!(
                    calls.lines().filter(|call| *call == start_command).count(),
                    1
                );
                let expected_command = if is_result {
                    "driver.manim-community.render.result"
                } else {
                    "driver.manim-community.render.status"
                };
                assert!(
                    calls.lines().any(|call| call == expected_command),
                    "fixture must reach the original {case} observation"
                );
            }
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn manim_terminal_host_failure_views_preserve_original_without_redispatch() {
        // Synthetic Broker views exercise the consumer and durable receipts.
        // The driver recorder has separate pure tests; no native Host runs here.
        for (start_command, state, original) in [
            (
                "driver.manim-community.static.render.start",
                "failed",
                Error::new(ErrorCode::PolicyDenied, "original bounded host denial"),
            ),
            (
                "driver.manim-community.editable.render.start",
                "unknown",
                Error::new(ErrorCode::Unavailable, "original host outcome lost").uncertain(),
            ),
            (
                "driver.manim-community.render.start",
                "failed",
                Error::new(ErrorCode::Unavailable, "inconsistent failed host view").uncertain(),
            ),
        ] {
            let (service, project, temp) = fixture_service();
            let (mut connection, calls_path) =
                fake_manim_render_connection(&temp, project.resource_key());
            let mut status = manim_fixture_job_data("status");
            status["state"] = json!(state);
            status["artifact"] = Value::Null;
            status["original_error"] = json!(original);
            let script = fs::read_to_string(&connection.executable).unwrap();
            let status_line = manim_fixture_data_line("status");
            assert_eq!(script.matches(status_line.as_str()).count(), 1);
            let replacement = format!(
                "data='{}'",
                serde_json::to_string(&status)
                    .unwrap()
                    .replace('\'', "'\\''")
            );
            fs::write(
                &connection.executable,
                script.replace(status_line.as_str(), &replacement),
            )
            .unwrap();
            connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
            let coordinator = ProductionCoordinator::new(service, connection).unwrap();
            let observed = coordinator
                .realize_prepared_manim_scene(
                    project.id,
                    &project.stamp(),
                    "original-manim-host-failure",
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
                        requires_camera_getters: false,
                        source_bundle: None,
                    },
                )
                .await
                .expect_err("terminal failure cannot become verified native media");
            let expected = if state == "unknown" || !original.outcome_known {
                original
                    .clone()
                    .uncertain()
                    .recipe_progress(1, start_command)
            } else {
                original.clone()
            };
            assert_eq!(
                serde_json::to_value(observed).unwrap(),
                serde_json::to_value(expected).unwrap()
            );
            let calls = fs::read_to_string(calls_path).unwrap();
            assert_eq!(
                calls.lines().filter(|call| *call == start_command).count(),
                1
            );
            assert_eq!(
                calls
                    .lines()
                    .filter(|call| *call == "driver.manim-community.render.status")
                    .count(),
                1
            );
            assert!(
                !calls
                    .lines()
                    .any(|call| call == "driver.manim-community.render.result")
            );
            let receipts = coordinator.receipts(project.id, 100).unwrap();
            let retained = receipts
                .iter()
                .find(|receipt| {
                    receipt.request_id == "original-manim-host-failure:status:0"
                        && receipt.stage == "completed"
                })
                .unwrap();
            assert_eq!(
                retained.payload["result"]["data"]["original_error"],
                json!(original)
            );
            assert_eq!(retained.payload["result"]["data"]["state"], state);
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn manim_known_timeout_polls_but_unknown_timeout_preserves_receipt_without_restart() {
        // The finite fake CLI returns one timeout, then a known terminal denial.
        // An incorrect retry still terminates, and the call/receipt counts expose it.
        let start_command = "driver.manim-community.camera.render.start";
        for outcome_known in [true, false] {
            let (service, project, temp) = fixture_service();
            let (mut connection, calls_path) =
                fake_manim_render_connection(&temp, project.resource_key());
            let original = Error::new(ErrorCode::Timeout, "original Manim status timeout")
                .recipe_progress(1, "original-status-read");
            let original = if outcome_known {
                original
            } else {
                original.uncertain()
            };
            let mut timeout = transport_reply("driver.manim-community.render.status");
            timeout.ok = false;
            timeout.data = None;
            timeout.error = Some(original.clone());
            let provenance = timeout.execution.provenance.as_mut().unwrap();
            provenance.provider_generation = Some(2);
            provenance.descriptor_sha256 = "cd".repeat(32);
            let denial = Error::new(
                ErrorCode::PolicyDenied,
                "original terminal denial after read",
            );
            let mut terminal = manim_fixture_job_data("status");
            terminal["state"] = json!("failed");
            terminal["artifact"] = Value::Null;
            terminal["original_error"] = json!(denial);
            let replacement = format!(
                "if [ ! -e \"$0.polled\" ]; then\n      : > \"$0.polled\"\n      printf '%s\\n' '{}'\n      exit 0\n    fi\n    data='{}'",
                serde_json::to_string(&timeout)
                    .unwrap()
                    .replace('\'', "'\\''"),
                serde_json::to_string(&terminal)
                    .unwrap()
                    .replace('\'', "'\\''")
            );
            let script = fs::read_to_string(&connection.executable).unwrap();
            let status_line = manim_fixture_data_line("status");
            assert_eq!(script.matches(status_line.as_str()).count(), 1);
            fs::write(
                &connection.executable,
                script.replace(status_line.as_str(), &replacement),
            )
            .unwrap();
            connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
            let coordinator = ProductionCoordinator::new(service, connection).unwrap();
            let observed = coordinator
                .realize_prepared_manim_scene(
                    project.id,
                    &project.stamp(),
                    "original-manim-timeout",
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
                        requires_camera_getters: true,
                        source_bundle: None,
                    },
                )
                .await
                .expect_err("the finite fixture ends with the original error or terminal denial");
            let expected = if outcome_known {
                denial
            } else {
                original
                    .clone()
                    .uncertain()
                    .recipe_progress(1, start_command)
            };
            assert_eq!(
                serde_json::to_value(observed).unwrap(),
                serde_json::to_value(expected).unwrap()
            );
            let calls = fs::read_to_string(calls_path).unwrap();
            assert_eq!(
                calls.lines().filter(|call| *call == start_command).count(),
                1
            );
            assert_eq!(
                calls
                    .lines()
                    .filter(|call| *call == "driver.manim-community.render.status")
                    .count(),
                if outcome_known { 2 } else { 1 }
            );
            assert!(
                !calls
                    .lines()
                    .any(|call| call == "driver.manim-community.render.result")
            );
            let receipts = coordinator.receipts(project.id, 100).unwrap();
            let stage = if outcome_known {
                "failed_known"
            } else {
                "outcome_unknown"
            };
            let retained = receipts
                .iter()
                .find(|receipt| {
                    receipt.request_id == "original-manim-timeout:status:0"
                        && receipt.stage == stage
                })
                .unwrap();
            assert_eq!(retained.payload["error"], json!(original));
            assert_eq!(
                receipts
                    .iter()
                    .any(|receipt| receipt.request_id == "original-manim-timeout:status:1"),
                outcome_known
            );
        }
    }

    #[cfg(unix)]
    pub(super) fn transport_reply(command: &str) -> Envelope {
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
    pub(super) fn fake_transport_connection(
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
    async fn production_transport_contradictory_result_fields_reject_without_redispatch() {
        for (command, mutation) in [
            ("driver.blender.collection.create", true),
            ("driver.motion-canvas.composition.inspect", false),
        ] {
            for (ok, has_data, exit_code) in [(true, true, 0), (true, false, 19), (false, true, 19)]
            {
                let mut envelope = transport_reply(command);
                envelope.ok = ok;
                envelope.data = has_data.then(|| json!({"changed": true}));
                envelope.error = Some(Error::new(
                    ErrorCode::ConsentRequired,
                    "typed error must not authenticate contradictory result fields",
                ));
                let error =
                    backend("Canonical production response has contradictory result fields");
                assert_transport_failure(
                    &serde_json::to_string(&envelope).unwrap(),
                    "",
                    exit_code,
                    command,
                    mutation,
                    if mutation { error.uncertain() } else { error },
                )
                .await;
            }
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn production_transport_unrequested_dry_run_rejects_before_terminal_admission() {
        for (command, mutation) in [
            ("driver.blender.collection.create", true),
            ("driver.motion-canvas.composition.inspect", false),
        ] {
            for failed in [false, true] {
                let mut envelope = transport_reply(command);
                if failed {
                    envelope.ok = false;
                    envelope.data = None;
                    envelope.error = Some(Error::new(
                        ErrorCode::ConsentRequired,
                        "typed error does not admit unrequested dry-run metadata",
                    ));
                }
                envelope.execution.dry_run = true;
                let error =
                    backend("Canonical production response reported an unrequested dry run");
                assert_transport_failure(
                    &serde_json::to_string(&envelope).unwrap(),
                    "",
                    if failed { 19 } else { 0 },
                    command,
                    mutation,
                    if mutation { error.uncertain() } else { error },
                )
                .await;
            }
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
    async fn assert_completed_replay_keeps_its_original_revision(mutation: bool) {
        use std::sync::atomic::Ordering;
        let (service, project, temp) = fixture_service();
        let connection = if mutation {
            fake_render_connection(&temp, project.resource_key())
        } else {
            fake_connection(&temp, project.resource_key())
        };
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let (command, args) = if mutation {
            (
                "driver.motion-canvas.render.start",
                json!({
                    "expected_fingerprint": "a".repeat(64),
                    "profile": {
                        "first_frame": 0, "end_frame_exclusive": 60,
                        "scale": "full", "transparent": false, "timeout_ms": 300_000
                    }
                }),
            )
        } else {
            ("driver.motion-canvas.composition.inspect", json!({}))
        };
        let key = "completed-original-revision";
        let first = coordinator
            .execute(
                project.id,
                &project.stamp(),
                key,
                command,
                args.clone(),
                mutation,
            )
            .await
            .unwrap();
        assert_eq!(first["replayed"], false);
        let retained = service
            .latest_production_receipt(project.id, key)
            .unwrap()
            .unwrap();
        assert_eq!(retained.revision, project.revision);
        let same_scope = coordinator
            .execute(
                project.id,
                &project.stamp(),
                key,
                command,
                args.clone(),
                mutation,
            )
            .await
            .unwrap();
        assert_eq!(same_scope["replayed"], true);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        let peer = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let changed = peer
            .apply(
                project.id,
                &project.stamp(),
                "peer-edit-after-completed-native-call",
                &motionwright_domain::Change::RenameProject {
                    title: "Later project revision".into(),
                },
            )
            .unwrap()
            .project;
        assert_eq!(changed.revision, project.revision + 1);
        let old_scope = coordinator
            .execute(
                project.id,
                &project.stamp(),
                key,
                command,
                args.clone(),
                mutation,
            )
            .await
            .unwrap_err();
        assert_eq!(old_scope.code, ErrorCode::StaleReference);
        let cross_revision = coordinator
            .execute(
                project.id,
                &changed.stamp(),
                key,
                command,
                args.clone(),
                mutation,
            )
            .await;
        let error = cross_revision.expect_err(
            "a current reference cannot rebind a completed native request from an older revision",
        );
        assert_eq!(error.code, ErrorCode::Conflict);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        assert_eq!(service.project(project.id).unwrap(), changed);
        assert_eq!(
            service.production_receipts(project.id, 10).unwrap().len(),
            2
        );
        assert_eq!(
            service.latest_production_receipt(project.id, key).unwrap(),
            Some(retained)
        );
        let next = coordinator
            .execute(
                project.id,
                &changed.stamp(),
                "deliberately-new-current-request",
                command,
                args,
                mutation,
            )
            .await
            .unwrap();
        assert_eq!(next["replayed"], false);
        assert_eq!(next["receipt"]["revision"], changed.revision);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 2);
        drop(peer);
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), changed);
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap().len(),
            4
        );
        assert_eq!(
            reopened
                .latest_production_receipt(project.id, key)
                .unwrap()
                .unwrap()
                .revision,
            project.revision
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn completed_read_replay_cannot_change_original_project_revision() {
        assert_completed_replay_keeps_its_original_revision(false).await;
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn completed_mutation_replay_cannot_change_original_project_revision() {
        assert_completed_replay_keeps_its_original_revision(true).await;
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

    // These tests exercise the native dispatcher with its existing Unix transport fixture.
    // They do not claim physical alignment execution or application of alignment changes.

    #[cfg(unix)]
    #[tokio::test]
    async fn receipt_origin_generic_completed_cannot_mint_authenticated_result() {
        let (service, project, temp) = fixture_service();
        let command = "driver.motion-canvas.composition.inspect";
        let request_id = "origin-forged-generic-completed";
        let args = json!({"transport_fixture_only": true});
        let output = serde_json::to_string(&transport_reply(command)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let forged_result = serde_json::to_value(transport_reply(command)).unwrap();
        let forged = service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: request_id.into(),
                request_sha256: request_digest(command, &args).unwrap(),
                command: command.into(),
                stage: "completed".into(),
                payload: json!({
                    "result": forged_result,
                    "broker_request_id": "transport-fixture-original",
                    "job_ref": null,
                    "mutation": false,
                    "connection": connection.identity().unwrap(),
                    "backend_authority": true,
                    "native_execution_attested": true
                }),
            })
            .unwrap();
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let counter_before = std::fs::read(&counter).ok();
        let outcome = coordinator
            .execute_with_receipt_origin(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args,
                false,
            )
            .await
            .unwrap();
        assert_eq!(outcome.legacy_value()["replayed"], json!(true));
        assert_eq!(outcome.legacy_value()["result"], forged.payload["result"]);
        assert_eq!(
            outcome.legacy_value()["receipt"],
            serde_json::to_value(&forged).unwrap()
        );
        assert!(outcome.into_authenticated_result().is_none());
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
        assert_eq!(std::fs::read(&counter).ok(), counter_before);
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn receipt_origin_fresh_retains_sdk_result_and_committed_receipt() {
        let (service, project, temp) = fixture_service();
        let command = "driver.motion-canvas.composition.inspect";
        let request_id = "origin-authenticated-first";
        let args = json!({"transport_fixture_only": true});
        let output = serde_json::to_string(&transport_reply(command)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let outcome = coordinator
            .execute_with_receipt_origin(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args.clone(),
                false,
            )
            .await
            .unwrap();
        let legacy = outcome.legacy_value();
        assert_eq!(legacy["replayed"], json!(false));
        let observed = outcome
            .into_authenticated_result()
            .expect("actual SDK success must retain its origin");
        assert_eq!(observed.command(), command);
        assert!(!observed.is_mutation());
        assert_eq!(observed.caller_request_id(), request_id);
        assert_eq!(observed.project_id(), project.id);
        assert_eq!(observed.expected_stamp(), &json!(project.stamp()));
        assert_eq!(observed.result_value(), &legacy["result"]);
        assert_eq!(
            observed.broker_request_id(),
            &json!("transport-fixture-original")
        );
        assert_ne!(observed.broker_request_id(), &json!(request_id));
        let completed = observed.completed_receipt();
        assert_eq!(completed.project_id, project.id);
        assert_eq!(completed.generation, project.generation);
        assert_eq!(completed.revision, project.revision);
        assert_eq!(completed.request_id, request_id);
        assert_eq!(completed.command, command);
        assert_eq!(completed.stage, "completed");
        assert_eq!(
            completed.request_sha256,
            request_digest(command, &args).unwrap()
        );
        assert_eq!(&completed.payload["result"], observed.result_value());
        assert_eq!(
            &completed.payload["broker_request_id"],
            observed.broker_request_id()
        );
        assert_eq!(serde_json::to_value(completed).unwrap(), legacy["receipt"]);
        assert_eq!(std::fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            1
        );

        let replay = coordinator
            .execute_with_receipt_origin(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args,
                false,
            )
            .await
            .unwrap();
        assert_eq!(replay.legacy_value()["replayed"], json!(true));
        assert_eq!(replay.legacy_value()["result"], legacy["result"]);
        assert_eq!(replay.legacy_value()["receipt"], legacy["receipt"]);
        assert!(replay.into_authenticated_result().is_none());
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            1
        );
        assert_eq!(std::fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn receipt_origin_restart_replay_cannot_reconstruct_authentication() {
        let (service, project, temp) = fixture_service();
        let command = "driver.motion-canvas.composition.inspect";
        let request_id = "origin-before-restart";
        let args = json!({"transport_fixture_only": true});
        let output = serde_json::to_string(&transport_reply(command)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let outcome = coordinator
            .execute_with_receipt_origin(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args.clone(),
                false,
            )
            .await
            .unwrap();
        let legacy = outcome.legacy_value();
        let observed = outcome
            .into_authenticated_result()
            .expect("initial transport result is observed");
        assert_eq!(
            serde_json::to_value(observed.completed_receipt()).unwrap(),
            legacy["receipt"]
        );
        assert_eq!(std::fs::read(&counter).unwrap(), b"invoked\n");
        drop(observed);
        drop(coordinator);
        drop(service);

        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let replay = recovered
            .execute_with_receipt_origin(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args,
                false,
            )
            .await
            .unwrap();
        assert_eq!(replay.legacy_value()["replayed"], json!(true));
        assert_eq!(replay.legacy_value()["result"], legacy["result"]);
        assert_eq!(replay.legacy_value()["receipt"], legacy["receipt"]);
        assert!(replay.into_authenticated_result().is_none());
        assert_eq!(
            recovered
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
        assert_eq!(std::fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[test]
    fn renderer_registry_uses_only_the_fixed_builtin_doctor_authority() {
        let authority = expected_authority("doctor").unwrap();
        assert_eq!(authority.provider, "semwright-core");
        assert_eq!(authority.source, SourceKind::Builtin);
        assert!(!authority.provider_generation_required);
        for command in [
            "doctor.extra",
            "doctor arbitrary",
            "driver.doctor",
            "capabilities.list",
        ] {
            assert!(expected_authority(command).is_none());
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn renderer_registry_transport_preserves_evidence_without_native_readiness() {
        let (service, project, temp) = fixture_service();
        let before = serde_json::to_value(&project).unwrap();
        let mut reply = transport_reply("doctor");
        reply.data = Some(super::renderer_registry::registry_test_data(vec![
            super::renderer_registry::registry_test_provider("driver:blender", true),
            super::renderer_registry::registry_test_provider("driver:manim-community", false),
        ]));
        let stdout = serde_json::to_string(&reply).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &stdout, "", 0);
        let client = ProductionClient::new(connection).unwrap();
        let stamp = motionwright_domain::RevisionStamp {
            resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
        };
        let registry = client.discover_renderer_registry(&stamp).await.unwrap();
        assert_eq!(std::fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(registry.broker_request_id(), "transport-fixture-original");
        assert_eq!(registry.catalog_revision(), 9);
        assert_eq!(registry.provider_version_for("blender"), Some("0.1.0"));
        let baseline = crate::creative_doctor::inspect_creative_runtime(
            &project,
            "synthetic-source-pin",
            crate::creative_doctor::CliEvidence::VersionCompatible,
            true,
        );
        let report = crate::creative_doctor::inspect_creative_runtime_with_registry(
            &project,
            "synthetic-source-pin",
            crate::creative_doctor::CliEvidence::VersionCompatible,
            true,
            registry,
        )
        .unwrap();
        assert!(report.broker_dispatch_performed);
        assert!(!report.ready_for_native_production);
        assert!(!report.install_performed);
        assert!(!report.authorizing_grant_created);
        assert!(
            report
                .renderers
                .iter()
                .all(|renderer| !renderer.provider_verified)
        );
        assert_eq!(
            report
                .renderers
                .iter()
                .map(|renderer| renderer.state)
                .collect::<Vec<_>>(),
            baseline
                .renderers
                .iter()
                .map(|renderer| renderer.state)
                .collect::<Vec<_>>(),
        );
        let connected = report
            .checks
            .iter()
            .find(|check| check.id == "renderer_registry_blender")
            .unwrap();
        assert_eq!(connected.status, "validated");
        assert!(connected.detail.contains("present_connected"));
        assert!(connected.detail.contains("provider_identity_version=0.1.0"));
        let missing = report
            .checks
            .iter()
            .find(|check| check.id == "renderer_registry_motion_canvas")
            .unwrap();
        assert!(missing.detail.contains("not_registered_current_broker"));
        assert!(
            report
                .checks
                .iter()
                .filter(|check| check.id.starts_with("native_runtime_"))
                .all(|check| check.status == "not_tested")
        );
        assert_eq!(
            report
                .checks
                .iter()
                .filter(|check| check.id.starts_with("native_runtime_"))
                .count(),
            4
        );
        assert_eq!(
            serde_json::to_value(service.project(project.id).unwrap()).unwrap(),
            before
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn renderer_registry_foreign_resource_is_rejected_before_cli_dispatch() {
        let (_service, project, temp) = fixture_service();
        let stdout = serde_json::to_string(&transport_reply("doctor")).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &stdout, "", 0);
        let client = ProductionClient::new(connection).unwrap();
        let stamp = motionwright_domain::RevisionStamp {
            resource: format!("{}-other", project.resource_key()),
            generation: project.generation,
            revision: project.revision,
        };
        let error = client.discover_renderer_registry(&stamp).await.unwrap_err();
        assert_eq!(error.code, ErrorCode::PolicyDenied);
        assert!(!counter.exists());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn renderer_registry_failed_authentication_or_data_never_proves_absence() {
        for case in [
            "provider",
            "source",
            "missing_provenance",
            "fake",
            "incomplete_registry",
        ] {
            let (service, project, temp) = fixture_service();
            let before = serde_json::to_value(&project).unwrap();
            let mut reply = transport_reply("doctor");
            reply.data = Some(super::renderer_registry::registry_test_data(vec![]));
            match case {
                "provider" => {
                    reply.execution.provenance.as_mut().unwrap().provider = "driver:blender".into()
                }
                "source" => {
                    reply.execution.provenance.as_mut().unwrap().source = SourceKind::Driver
                }
                "missing_provenance" => reply.execution.provenance = None,
                "fake" => reply.data.as_mut().unwrap()["fake"] = serde_json::json!(true),
                "incomplete_registry" => {
                    reply.data.as_mut().unwrap()["providers"]
                        .as_object_mut()
                        .unwrap()
                        .remove("providers");
                }
                _ => unreachable!(),
            }
            let stdout = serde_json::to_string(&reply).unwrap();
            let (connection, counter) =
                fake_transport_connection(&temp, project.resource_key(), &stdout, "", 0);
            let client = ProductionClient::new(connection).unwrap();
            let stamp = motionwright_domain::RevisionStamp {
                resource: project.resource_key(),
                generation: project.generation,
                revision: project.revision,
            };
            assert!(
                client.discover_renderer_registry(&stamp).await.is_err(),
                "case: {case}"
            );
            assert_eq!(
                std::fs::read(&counter).unwrap(),
                b"invoked\n",
                "case: {case}"
            );
            assert_eq!(
                serde_json::to_value(service.project(project.id).unwrap()).unwrap(),
                before,
                "case: {case}"
            );
        }
    }
    #[cfg(unix)]
    #[tokio::test]
    async fn manim_runtime_version_transport_preserves_observation_and_project() {
        for (version, expected_state, unavailable) in [
            (
                Some("0.21.0"),
                ManimRuntimeVersionState::ObservedCompatible,
                false,
            ),
            (
                Some("0.20.1"),
                ManimRuntimeVersionState::ObservedIncompatible,
                false,
            ),
            (None, ManimRuntimeVersionState::PackageNotFound, false),
            (None, ManimRuntimeVersionState::NotObserved, true),
        ] {
            let (service, project, temp) = fixture_service();
            let mut envelope = transport_reply("driver.manim-community.doctor");
            let mut data = renderer_versions::manim_runtime_test_data(version);
            if unavailable {
                data["runtime_probe"]["state"] = json!("not_observed");
                data["runtime_probe"]["source"] = json!("none");
                data["runtime_probe"]["reason_code"] = json!("host_probe_failed");
            }
            envelope.data = Some(data);
            let output = serde_json::to_string(&envelope).unwrap();
            let (connection, counter) =
                fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let observation = coordinator
                .observe_manim_runtime_version(project.id, &project.stamp())
                .await
                .unwrap();
            assert_eq!(observation.state(), expected_state);
            assert_eq!(observation.observed_version(), version);
            assert_eq!(
                observation.version_compatible(),
                version.map(|value| value == "0.21.0")
            );
            assert_eq!(observation.provider_generation(), 17);
            assert_eq!(
                observation.broker_request_id(),
                "transport-fixture-original"
            );
            assert!(observation.matches_scope(&project.stamp()));
            let evidence = serde_json::to_value(&observation).unwrap();
            assert_eq!(evidence["observation_scope"], "declared_runtime");
            assert_eq!(evidence["render_performed"], false);
            assert_eq!(evidence["scene_mutation_performed"], false);
            assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
            assert_eq!(service.project(project.id).unwrap(), project);
            assert!(
                service
                    .production_receipts(project.id, 10)
                    .unwrap()
                    .is_empty()
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn manim_runtime_version_transport_rejects_foreign_or_unbound_observations() {
        let mut base = transport_reply("driver.manim-community.doctor");
        base.data = Some(renderer_versions::manim_runtime_test_data(Some("0.21.0")));
        let base = serde_json::to_value(base).unwrap();
        for (pointer, replacement) in [
            ("/execution/provenance/provider", json!("driver:blender")),
            ("/execution/provenance/source", json!(SourceKind::Builtin)),
            ("/execution/provenance/provider_generation", Value::Null),
            ("/execution/provenance/descriptor_sha256", json!("invalid")),
            ("/execution/dry_run", json!(true)),
            ("/data/runtime_probe/compatible", json!(false)),
        ] {
            let (service, project, temp) = fixture_service();
            let mut envelope = base.clone();
            *envelope.pointer_mut(pointer).unwrap() = replacement;
            let output = serde_json::to_string(&envelope).unwrap();
            let (connection, counter) =
                fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            assert!(
                coordinator
                    .observe_manim_runtime_version(project.id, &project.stamp(),)
                    .await
                    .is_err(),
                "{pointer}"
            );
            assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
            assert_eq!(service.project(project.id).unwrap(), project);
            assert!(
                service
                    .production_receipts(project.id, 10)
                    .unwrap()
                    .is_empty()
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn manim_runtime_version_scope_and_transport_error_never_claim_absence() {
        let (service, project, temp) = fixture_service();
        let original = Error::new(ErrorCode::NotFound, "original provider lookup failure");
        let mut envelope = transport_reply("driver.manim-community.doctor");
        envelope.ok = false;
        envelope.data = None;
        envelope.error = Some(original.clone());
        let output = serde_json::to_string(&envelope).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 19);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let mut stale = project.stamp();
        stale.revision += 1;
        let error = coordinator
            .observe_manim_runtime_version(project.id, &stale)
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
        assert!(!counter.exists());
        let error = coordinator
            .observe_manim_runtime_version(project.id, &project.stamp())
            .await
            .unwrap_err();
        assert_eq!(
            serde_json::to_value(&error).unwrap(),
            serde_json::to_value(original).unwrap()
        );
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(service.project(project.id).unwrap(), project);
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
    }
    #[test]
    fn prepared_original_source_inputs_reject_cross_profile_dispatch() {
        let graph: crate::manim_graph_projection::OriginalGraphBundleInput =
            serde_json::from_value(json!({
                "root": "output", "relative_path": "source.mwgraph.zip",
                "sha256": "a".repeat(64), "bytes": 1,
            }))
            .unwrap();
        let graph = PreparedManimSourceInput::Graph(graph);
        assert!(graph.start_value(manim_graph::START_COMMAND).is_ok());
        assert_eq!(
            graph
                .start_value("driver.manim-community.editable.render.start")
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
        let editable: crate::manim_editable_projection::OriginalEditableBundleInput =
            serde_json::from_value(json!({
                "root": "output", "relative_path": "source.mwedit.zip",
                "sha256": "b".repeat(64), "bytes": 1,
            }))
            .unwrap();
        let editable = PreparedManimSourceInput::Editable(editable);
        assert!(
            editable
                .start_value("driver.manim-community.editable.render.start")
                .is_ok()
        );
        assert_eq!(
            editable
                .start_value(manim_graph::START_COMMAND)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
        for command in [
            "driver.manim-community.render.start",
            "driver.manim-community.camera.render.start",
            "driver.manim-community.static.render.start",
        ] {
            assert!(graph.start_value(command).is_err());
            assert!(editable.start_value(command).is_err());
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_window_unknown_original_stops_before_any_client_dispatch() {
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let project = persist_native_boundary_test_draft(&service, project);
        let connection = fake_render_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let expected = RevisionStamp::from(&project);
        let request = MotionCanvasWindowRequest {
            deliverable_id: project.deliverables[0].id,
            source_scene_id: project.scenes[0].id,
            source_window: motionwright_domain::SourceFrameWindow { start: 10, end: 20 },
            options: FilmBuildOptions {
                frame_rate: Rate::new(30, 1).unwrap(),
                font_family: crate::film::MOTION_CANVAS_FONT_FAMILY.into(),
                mono_font_family: crate::film::MOTION_CANVAS_MONO_FONT_FAMILY.into(),
                scene_intents: vec![crate::film::SceneFilmIntent {
                    scene_id: project.scenes[0].id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                }],
            },
        };
        for (ordinal, stage) in [
            "outcome_unknown",
            "dispatching",
            "completed",
            "failed_known",
        ]
        .into_iter()
        .enumerate()
        {
            let parent = format!("window-original-{ordinal}");
            service.append_production_receipt(ProductionReceiptInput {
                project_id:project.id,generation:project.generation,revision:project.revision,
                request_id:format!("{parent}:motion:0:render-start"),request_sha256:"ab".repeat(32),
                command:"driver.motion-canvas.render.start".into(),stage:stage.into(),
                payload:json!({"mutation":true,"error":Error::new(ErrorCode::Timeout,"unit original").uncertain()}),
            }).unwrap();
            let before = service.production_receipts(project.id, 512).unwrap();
            let error = coordinator
                .render_motion_canvas_window(project.id, &expected, &parent, &request)
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::Conflict);
            assert_eq!(
                error.outcome_known,
                !matches!(stage, "outcome_unknown" | "dispatching")
            );
            assert_eq!(
                coordinator
                    .client
                    .execute_calls
                    .load(std::sync::atomic::Ordering::SeqCst),
                0
            );
            assert_eq!(
                service.production_receipts(project.id, 512).unwrap(),
                before
            );
        }
    }

    #[test]
    fn verified_artifact_reader_keeps_exact_bytes_and_rejects_hash_and_budget_changes() {
        let temp = tempfile::tempdir().unwrap();
        let bytes = b"original bounded native bytes";
        fs::write(temp.path().join("original.bin"), bytes).unwrap();
        let digest = hex::encode(Sha256::digest(bytes));
        assert_eq!(
            read_verified_production_artifact(
                temp.path(),
                "original.bin",
                &digest,
                bytes.len() as u64
            )
            .unwrap(),
            bytes
        );
        for limit in [0, bytes.len() as u64 - 1, u64::MAX] {
            assert!(
                read_verified_production_artifact(temp.path(), "original.bin", &digest, limit)
                    .is_err()
            );
        }
        assert!(
            read_verified_production_artifact(temp.path(), "original.bin", &"00".repeat(32), 1024)
                .is_err()
        );
        fs::write(
            temp.path().join("original.bin"),
            b"changed bounded native bytes!",
        )
        .unwrap();
        assert!(
            read_verified_production_artifact(temp.path(), "original.bin", &digest, 1024).is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn verified_artifact_open_rejects_replacement_growth_links_and_owner_path_changes() {
        use std::os::unix::fs::symlink;
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("original.bin");
        let bytes = b"same-sized original bytes";
        fs::write(&path, bytes).unwrap();
        let before = safe_metadata(&path).unwrap();
        fs::rename(&path, temp.path().join("retained-original.bin")).unwrap();
        fs::write(&path, bytes).unwrap();
        // The replacement has the same contents and length; device/inode must
        // still reject it against the observed original metadata.
        assert!(open_verified_artifact_input(&path, &before).is_err());
        let before_growth = safe_metadata(&path).unwrap();
        fs::write(&path, b"a longer substituted source than the original").unwrap();
        assert!(open_verified_artifact_input(&path, &before_growth).is_err());
        fs::remove_file(&path).unwrap();
        symlink(temp.path().join("retained-original.bin"), &path).unwrap();
        assert!(open_verified_artifact_input(&path, &before).is_err());
        let digest = hex::encode(Sha256::digest(bytes));
        assert!(
            read_verified_production_artifact(temp.path(), "original.bin", &digest, 1024).is_err()
        );
        fs::remove_file(&path).unwrap();
        fs::create_dir(temp.path().join("owner")).unwrap();
        fs::write(temp.path().join("owner/original.bin"), bytes).unwrap();
        fs::rename(
            temp.path().join("owner"),
            temp.path().join("retained-owner"),
        )
        .unwrap();
        symlink(
            temp.path().join("retained-owner"),
            temp.path().join("owner"),
        )
        .unwrap();
        assert!(
            read_verified_production_artifact(
                &temp.path().join("owner"),
                "original.bin",
                &digest,
                1024
            )
            .is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn verified_artifact_fifo_substitution_is_rejected_under_an_external_deadline() {
        use std::os::unix::ffi::OsStrExt;
        const CHILD_ENV: &str = "MOTIONWRIGHT_VERIFIED_ARTIFACT_FIFO_CHILD";
        if let Some(directory) = std::env::var_os(CHILD_ENV) {
            let path = PathBuf::from(directory).join("original.bin");
            let before = safe_metadata(&path).unwrap();
            assert!(before.is_file());
            fs::remove_file(&path).unwrap();
            let raw = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(raw.as_ptr(), 0o600) }, 0);
            // No writer exists. The old plain open would wait indefinitely.
            assert!(open_verified_artifact_input(&path, &before).is_err());
            println!("verified-artifact-fifo-rejected-before-read");
            return;
        }
        struct OwnedChild(Option<std::process::Child>);
        impl Drop for OwnedChild {
            fn drop(&mut self) {
                if let Some(child) = self.0.as_mut() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
        let temp = tempfile::tempdir().unwrap();
        fs::write(temp.path().join("original.bin"), b"original").unwrap();
        let child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact","production::tests::verified_artifact_fifo_substitution_is_rejected_under_an_external_deadline","--nocapture"])
            .env(CHILD_ENV,temp.path()).stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).spawn().unwrap();
        let mut owned = OwnedChild(Some(child));
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut deadline_exceeded = false;
        loop {
            if owned.0.as_mut().unwrap().try_wait().unwrap().is_some() {
                break;
            }
            if std::time::Instant::now() >= deadline {
                deadline_exceeded = true;
                owned.0.as_mut().unwrap().kill().unwrap();
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let status = owned.0.as_mut().unwrap().wait().unwrap();
        let mut stdout = Vec::new();
        let mut stderr = Vec::new();
        owned
            .0
            .as_mut()
            .unwrap()
            .stdout
            .take()
            .unwrap()
            .take(64 * 1024)
            .read_to_end(&mut stdout)
            .unwrap();
        owned
            .0
            .as_mut()
            .unwrap()
            .stderr
            .take()
            .unwrap()
            .take(64 * 1024)
            .read_to_end(&mut stderr)
            .unwrap();
        // Keep ownership through wait and both reads; panic still runs cleanup.
        drop(owned.0.take());
        assert!(
            !deadline_exceeded,
            "substituted FIFO blocked the artifact opener; child terminated; stderr={}",
            String::from_utf8_lossy(&stderr)
        );
        assert!(
            status.success(),
            "artifact opener child failed: {}",
            String::from_utf8_lossy(&stderr)
        );
        assert!(
            String::from_utf8_lossy(&stdout)
                .contains("verified-artifact-fifo-rejected-before-read")
        );
    }

    #[cfg(unix)]
    fn persist_native_boundary_test_draft(
        service: &StudioService,
        project: motionwright_domain::Project,
    ) -> motionwright_domain::Project {
        use motionwright_domain::{SceneBoundary, SceneBoundaryDefinition, SceneBoundaryInput};
        service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-boundary-fixture-draft",
                &Change::UpsertSceneBoundary {
                    boundary: Box::new(SceneBoundary {
                        version: motionwright_domain::SCENE_BOUNDARY_VERSION,
                        id: Uuid::now_v7(),
                        scene_id: project.scenes[0].id,
                        input: SceneBoundaryInput::SelfContained,
                        definition: SceneBoundaryDefinition::DraftIncomplete {
                            source: None,
                            entry: None,
                            exit: None,
                        },
                    }),
                },
            )
            .unwrap()
            .project
    }

    #[cfg(unix)]
    fn saturate_native_boundary_test_custody(
        coordinator: &ProductionCoordinator,
    ) -> Vec<receipt_custody::ReceiptReservation> {
        let limit = coordinator.receipt_custody.snapshot()["slot_limit"]
            .as_u64()
            .unwrap();
        let mut held = Vec::new();
        for index in 0..=limit {
            match coordinator.receipt_custody.reserve(
                true,
                &format!("native-boundary-pressure-{index}"),
                "driver.motion-canvas.render.start",
                None,
            ) {
                Ok(reservation) => held.push(reservation),
                Err(error) => {
                    assert_eq!(error.code, ErrorCode::ResourceExhausted);
                    assert!(!held.is_empty());
                    return held;
                }
            }
        }
        panic!("fixture failed to exhaust receipt custody");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_boundary_completed_receipt_precedes_new_effect_guard() {
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let project = persist_native_boundary_test_draft(&service, project);
        let command = "driver.motion-canvas.render.start";
        let request_id = "native-boundary-retained-completed";
        let args = json!({
            "expected_fingerprint": "a".repeat(64),
            "profile": {
                "first_frame": 0, "end_frame_exclusive": 60,
                "scale": "full", "transparent": false, "timeout_ms": 300_000
            }
        });
        // Synthetic persisted terminal fixture: no native render seeds this result.
        let original_result = json!({
            "data": {"job_ref": "retained-fixture-job"},
            "synthetic_receipt_fixture": true
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
        service
            .append_production_receipt(ProductionReceiptInput {
                stage: "completed".into(),
                payload: json!({"mutation": true, "result": original_result}),
                ..original_claim
            })
            .unwrap();
        let before = service.production_receipts(project.id, 20).unwrap();
        let connection = fake_render_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let held = saturate_native_boundary_test_custody(&coordinator);
        let custody = coordinator.receipt_custody.snapshot();

        let replayed = coordinator
            .execute(
                project.id,
                &project.stamp(),
                request_id,
                command,
                args.clone(),
                true,
            )
            .await
            .unwrap();
        assert_eq!(replayed["replayed"], true);
        assert_eq!(replayed["result"], original_result);
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "native-boundary-new-effect",
                command,
                args,
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.outcome_known);
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
        assert_eq!(service.production_receipts(project.id, 20).unwrap(), before);
        assert_eq!(coordinator.receipt_custody.snapshot(), custody);
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(held);
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        assert_eq!(
            reopened.production_receipts(project.id, 20).unwrap(),
            before
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_boundary_motion_history_precedes_admission_and_font_staging() {
        let cases = [
            (false, "font-import:1", "dispatching", true, true),
            (false, "plan", "outcome_unknown", false, true),
            (true, "font-project-create", "dispatching", true, true),
            (true, "font-import:0", "outcome_unknown", true, true),
            (false, "plan", "dispatching", false, false),
            (true, "plan", "dispatching", false, false),
        ];
        for (index, (window, suffix, stage, mutation, unresolved)) in cases.into_iter().enumerate()
        {
            let (service, initial, temp) = fixture_service();
            let project = motion_canvas_fixture(&service, initial);
            let project = persist_native_boundary_test_draft(&service, project);
            let parent = format!("native-boundary-history-{index}");
            let command = match suffix {
                "font-project-create" => "driver.motion-canvas.project.create",
                "font-import:0" | "font-import:1" => "driver.motion-canvas.asset.import",
                "plan" => "driver.motion-canvas.composition.plan",
                _ => unreachable!(),
            };
            service
                .append_production_receipt(ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: format!("{parent}:motion:0:{suffix}"),
                    request_sha256: "ab".repeat(32),
                    command: command.into(),
                    stage: stage.into(),
                    payload: json!({"mutation": mutation, "synthetic_receipt_fixture": true}),
                })
                .unwrap();
            let connection = fake_render_connection(&temp, project.resource_key());
            let output_root = connection.output_root.clone();
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let before = service.production_receipts(project.id, 20).unwrap();
            let custody = coordinator.receipt_custody.snapshot();
            let output_before = fs::read_dir(&output_root)
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect::<BTreeSet<_>>();
            let options = FilmBuildOptions {
                frame_rate: Rate::new(30, 1).unwrap(),
                font_family: crate::film::MOTION_CANVAS_FONT_FAMILY.into(),
                mono_font_family: crate::film::MOTION_CANVAS_MONO_FONT_FAMILY.into(),
                scene_intents: vec![crate::film::SceneFilmIntent {
                    scene_id: project.scenes[0].id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                }],
            };
            let error = if window {
                coordinator
                    .render_motion_canvas_window(
                        project.id,
                        &project.stamp(),
                        &parent,
                        &MotionCanvasWindowRequest {
                            deliverable_id: project.deliverables[0].id,
                            source_scene_id: project.scenes[0].id,
                            source_window: motionwright_domain::SourceFrameWindow {
                                start: 10,
                                end: 20,
                            },
                            options,
                        },
                    )
                    .await
                    .unwrap_err()
            } else {
                coordinator
                    .render_motion_canvas_segments(
                        project.id,
                        &project.stamp(),
                        &parent,
                        project.deliverables[0].id,
                        &options,
                    )
                    .await
                    .unwrap_err()
            };
            assert_eq!(
                error.code,
                if unresolved {
                    ErrorCode::Conflict
                } else {
                    ErrorCode::InvalidArgument
                },
                "case {index}"
            );
            assert_eq!(error.outcome_known, !unresolved, "case {index}");
            assert_eq!(
                coordinator
                    .client
                    .execute_calls
                    .load(std::sync::atomic::Ordering::SeqCst),
                0
            );
            assert_eq!(service.production_receipts(project.id, 20).unwrap(), before);
            assert_eq!(coordinator.receipt_custody.snapshot(), custody);
            assert_eq!(service.project(project.id).unwrap(), project);
            assert_eq!(
                fs::read_dir(&output_root)
                    .unwrap()
                    .map(|entry| entry.unwrap().file_name())
                    .collect::<BTreeSet<_>>(),
                output_before
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn mlt_audio_profile_is_not_a_visual_grant_and_unknown_precedes_scope() {
        use std::sync::{Arc, atomic::Ordering};
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let project = persist_native_boundary_test_draft(&service, project);
        let connection = fake_render_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: crate::film::MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: crate::film::MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: vec![crate::film::SceneFilmIntent {
                scene_id: project.scenes[0].id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            }],
        };
        // Private synthetic dispatch context; no native render/preflight is claimed.
        let mut scoped = coordinator.clone();
        scoped.scene_boundary_scope = Some(Arc::new(
            crate::native_scene_boundary::ExecutionScope::mlt_motion(
                &project,
                project.deliverables[0].id,
                &options,
                &[project.scenes[0].id],
            ),
        ));
        let command = "driver.mlt-video.frames.encode";
        let original_id = "mlt-boundary-original-unknown";
        let args = json!({
            "root": "output", "manifest_path": "render-fixture/artifact-manifest.json",
            "expected_manifest_sha256": "ab".repeat(32),
            "output_path": "fixture-only.mkv", "max_bytes": 1024
        });
        let claim = ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: original_id.into(),
            request_sha256: request_digest(command, &args).unwrap(),
            command: command.into(),
            stage: "dispatching".into(),
            payload: json!({"mutation": true, "synthetic_receipt_fixture": true}),
        };
        service.append_production_receipt(claim.clone()).unwrap();
        service
            .append_production_receipt(ProductionReceiptInput {
                stage: "outcome_unknown".into(),
                payload: json!({
                    "mutation": true,
                    "error": Error::new(ErrorCode::Timeout, "synthetic original").uncertain()
                }),
                ..claim
            })
            .unwrap();
        let before = service.production_receipts(project.id, 20).unwrap();
        let held = saturate_native_boundary_test_custody(&coordinator);
        let custody = coordinator.receipt_custody.snapshot();
        // Admission-only control reaches capacity, never driver argument validation or transport.
        let audio = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "mlt-boundary-audio-only",
                "driver.mlt-video.render.start",
                json!({
                    "project": "project:fixture", "expected_revision": "fixture-revision",
                    "sequence": "sequence:fixture", "profile": "audio-wav",
                    "output": "fixture-only.wav"
                }),
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(audio.code, ErrorCode::ResourceExhausted);
        assert!(audio.outcome_known);
        let mut forged_audio = args.clone();
        forged_audio["profile"] = json!("audio-wav");
        let visual = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "mlt-boundary-false-audio",
                command,
                forged_audio,
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(visual.code, ErrorCode::Unsupported);
        assert!(visual.outcome_known);
        let original = scoped
            .execute(
                project.id,
                &project.stamp(),
                original_id,
                command,
                args.clone(),
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(original.code, ErrorCode::Conflict);
        assert!(!original.outcome_known);
        let fresh = scoped
            .execute(
                project.id,
                &project.stamp(),
                "mlt-boundary-fresh",
                command,
                args,
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(fresh.code, ErrorCode::InvalidArgument);
        assert!(fresh.outcome_known);
        assert!(fresh.message.contains("SceneBoundary"));
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(service.production_receipts(project.id, 20).unwrap(), before);
        assert_eq!(coordinator.receipt_custody.snapshot(), custody);
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(held);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_boundary_typed_entrypoints_preserve_prior_unknown_before_draft_guard() {
        use motionwright_domain::RendererKind;
        use std::{collections::BTreeMap, ffi::OsString, path::Path, sync::atomic::Ordering};
        fn output_files(root: &Path) -> BTreeMap<OsString, Vec<u8>> {
            fs::read_dir(root)
                .unwrap()
                .map(|entry| {
                    let entry = entry.unwrap();
                    assert!(fs::symlink_metadata(entry.path()).unwrap().is_file());
                    (entry.file_name(), fs::read(entry.path()).unwrap())
                })
                .collect()
        }
        for (case_name, blender, export_unknown) in [
            ("manim-static-start", false, false),
            ("blender-object-create", true, false),
            ("blender-export", true, true),
        ] {
            let (service, initial, temp) = fixture_service();
            let mut project = motion_canvas_fixture(&service, initial);
            let scene_id = project.scenes[0].id;
            let renderer = if blender {
                RendererKind::Blender
            } else {
                RendererKind::ManimCommunity
            };
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    "boundary-typed-fixture-renderer",
                    &Change::SetSceneRenderer { scene_id, renderer },
                )
                .unwrap()
                .project;
            let original_node = project.scenes[0].nodes[0].clone();
            let mut rectangle = original_node.clone();
            rectangle.id = Uuid::now_v7();
            rectangle.name = "Native boundary rectangle".into();
            rectangle.kind = "rectangle".into();
            rectangle.text = None;
            rectangle.parent_id = None;
            rectangle.rotation_deg = 0.0;
            rectangle.opacity = 1.0;
            rectangle.relations.clear();
            rectangle.keyframes.clear();
            rectangle.property_locks.clear();
            rectangle.style.stroke = None;
            rectangle.style.stroke_width = 0.0;
            rectangle.style.font_family = None;
            rectangle.style.font_size = None;
            rectangle.style.font_weight = None;
            rectangle.style.line_height = None;
            rectangle.style.blend_mode = BlendMode::Normal;
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    "boundary-typed-fixture-remove-text",
                    &Change::RemoveCanvasNode {
                        scene_id,
                        node_id: original_node.id,
                    },
                )
                .unwrap()
                .project;
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    "boundary-typed-fixture-add-rectangle",
                    &Change::AddCanvasNode {
                        scene_id,
                        node: rectangle,
                    },
                )
                .unwrap()
                .project;
            let flat_plan = if blender {
                let plan = build_blender_contribution(&project, scene_id).unwrap();
                assert_eq!(plan.objects.len(), 1);
                Some(plan)
            } else {
                None
            };
            let project = persist_native_boundary_test_draft(&service, project);
            let expected = project.stamp();
            let request_id = format!("boundary-typed-{case_name}");
            let mut calls: Vec<(&str, &str, Value, &str)> = Vec::new();
            if let Some(plan) = &flat_plan {
                let object = &plan.objects[0];
                calls.push((
                    "collection",
                    "driver.blender.collection.create",
                    json!({"name": &plan.collection_name}),
                    "completed",
                ));
                calls.push(("object:0:create", "driver.blender.object.create",
                    json!({"name": &object.name, "primitive": object.primitive.as_driver_name(), "location": object.location}),
                    if export_unknown { "completed" } else { "outcome_unknown" }));
                if export_unknown {
                    calls.extend([
                        ("object:0:transform", "driver.blender.object.transform",
                            json!({"name": &object.name, "location": object.location, "rotation": object.rotation, "scale": object.scale}), "completed"),
                        ("object:0:link", "driver.blender.collection.link",
                            json!({"object": &object.name, "collection": &plan.collection_name}), "completed"),
                        ("object:0:material", "driver.blender.material.create",
                            json!({"name": &object.material_name, "color": object.color_rgba, "roughness": 0.45, "metallic": 0.0}), "completed"),
                        ("object:0:material-assign", "driver.blender.material.assign",
                            json!({"object": &object.name, "material": &object.material_name}), "completed"),
                        ("export", "driver.blender.export.glb",
                            json!({"collection": &plan.collection_name, "path": blender_export_path(plan), "animations": false}), "outcome_unknown"),
                    ]);
                }
            } else {
                // Opaque original fixture: no Manim bundle or native render is claimed.
                calls.push((
                    "start",
                    "driver.manim-community.static.render.start",
                    json!({"synthetic_retained_static_start": true}),
                    "outcome_unknown",
                ));
            }
            for (suffix, command, args, stage) in calls {
                let claim = ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: format!("{request_id}:{suffix}"),
                    request_sha256: request_digest(command, &args).unwrap(),
                    command: command.into(),
                    stage: "dispatching".into(),
                    payload: json!({"mutation": true, "synthetic_receipt_fixture": true}),
                };
                service.append_production_receipt(claim.clone()).unwrap();
                let payload = if stage == "completed" {
                    json!({"mutation": true, "result": {"data": {}, "synthetic_receipt_fixture": true}})
                } else {
                    json!({"mutation": true, "error": Error::new(
                        ErrorCode::Timeout, "retained unknown typed-entrypoint fixture"
                    ).uncertain()})
                };
                service
                    .append_production_receipt(ProductionReceiptInput {
                        stage: stage.into(),
                        payload,
                        ..claim
                    })
                    .unwrap();
            }
            let before = service.production_receipts(project.id, 64).unwrap();
            let connection = fake_render_connection(&temp, project.resource_key());
            let output_root = connection.output_root.clone();
            fs::write(
                output_root.join("retained-boundary-fixture.bin"),
                b"original output must remain unchanged",
            )
            .unwrap();
            let files_before = output_files(&output_root);
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let held = saturate_native_boundary_test_custody(&coordinator);
            let custody_before = coordinator.receipt_custody.snapshot();
            let outcome = if blender {
                coordinator
                    .realize_blender_scene(project.id, &expected, &request_id, scene_id)
                    .await
            } else {
                coordinator
                    .realize_manim_static_scene(
                        project.id,
                        &expected,
                        &request_id,
                        scene_id,
                        project.deliverables[0].id,
                    )
                    .await
            };
            let error = outcome.unwrap_err();
            assert_eq!(error.code, ErrorCode::Conflict, "{case_name}");
            assert!(!error.outcome_known, "{case_name}");
            assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
            assert_eq!(service.production_receipts(project.id, 64).unwrap(), before);
            assert_eq!(coordinator.receipt_custody.snapshot(), custody_before);
            assert_eq!(service.project(project.id).unwrap(), project);
            assert_eq!(output_files(&output_root), files_before);
            drop(held);
            drop(coordinator);
            drop(service);
            let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
            assert_eq!(
                reopened.production_receipts(project.id, 64).unwrap(),
                before
            );
            assert_eq!(reopened.project(project.id).unwrap(), project);
        }
    }

    // A4 Native SceneBoundary late-observation causal tests.
    // Synthetic source fixtures; compilation and execution are tracked externally.
    // Insert this submodule inside production.rs::tests.
    // Existing Native17 tests and fixtures remain unchanged.
    // Synthetic persisted receipts are not evidence of a native render.

    #[cfg(unix)]
    mod late_observation_boundary_tests {
        use super::*;
        use motionwright_domain::{Project, RendererKind};
        use motionwright_service::{ProductionReceipt, ProductionReceiptWatermark};
        use std::{
            collections::BTreeMap,
            ffi::OsString,
            path::Path,
            sync::{
                Arc,
                atomic::{AtomicUsize, Ordering},
            },
        };

        #[derive(Clone, Copy, Debug, PartialEq, Eq)]
        enum Route {
            Manim,
            Full,
            Window,
            Hyperframes,
        }

        #[derive(Clone, Copy, Debug, PartialEq, Eq)]
        enum Head {
            Unknown,
            CompletedAfterUnknown,
            FailedKnownAfterUnknown,
            ReadDispatching,
        }

        #[derive(Clone, Copy, Debug, PartialEq, Eq)]
        enum Origin {
            Current,
            Neighbor,
            OldRun,
        }

        #[derive(Clone, Copy, Debug)]
        struct Case {
            suffix: &'static str,
            command: &'static str,
            head: Head,
            origin: Origin,
            padding: bool,
        }

        impl Case {
            const fn new(suffix: &'static str, command: &'static str) -> Self {
                Self {
                    suffix,
                    command,
                    head: Head::Unknown,
                    origin: Origin::Current,
                    padding: false,
                }
            }

            fn head(mut self, value: Head) -> Self {
                self.head = value;
                self
            }

            fn origin(mut self, value: Origin) -> Self {
                self.origin = value;
                self
            }

            fn padded(mut self) -> Self {
                self.padding = true;
                self
            }
        }

        fn complete_snapshot(
            service: &StudioService,
            project_id: Uuid,
        ) -> (ProductionReceiptWatermark, Vec<ProductionReceipt>) {
            let watermark = service.production_receipt_watermark(project_id).unwrap();
            let mut before = None;
            let mut receipts = Vec::new();

            loop {
                // Deliberately independent of the production scanner's page size.
                let page = service
                    .production_receipts_snapshot_page(project_id, &watermark, before, 113)
                    .unwrap();
                if page.is_empty() {
                    break;
                }
                before = page.last().map(|receipt| receipt.id);
                receipts.extend(page);
            }

            assert_eq!(receipts.len() as u64, watermark.count);
            assert_eq!(
                service.production_receipt_watermark(project_id).unwrap(),
                watermark
            );
            (watermark, receipts)
        }

        fn output_files(root: &Path) -> BTreeMap<OsString, Vec<u8>> {
            fs::read_dir(root)
                .unwrap()
                .map(|entry| {
                    let entry = entry.unwrap();
                    assert!(fs::symlink_metadata(entry.path()).unwrap().is_file());
                    (entry.file_name(), fs::read(entry.path()).unwrap())
                })
                .collect()
        }

        fn append_record(
            service: &StudioService,
            project: &Project,
            request_id: &str,
            command: &str,
            args: &Value,
            mutation: bool,
            stage: &str,
        ) {
            let payload = match stage {
                "dispatching" => json!({
                    "mutation": mutation,
                    "synthetic_receipt_fixture": true
                }),
                "completed" => {
                    let mut envelope = transport_reply(command);
                    envelope.data = Some(json!({
                        "job_ref": "retained-observation-fixture-job",
                        "state": "queued"
                    }));
                    json!({
                        "mutation": mutation,
                        "result": envelope,
                        "synthetic_receipt_fixture": true
                    })
                }
                "outcome_unknown" => json!({
                    "mutation": mutation,
                    "error": Error::new(
                        ErrorCode::Timeout,
                        "retained late-observation fixture"
                    ).uncertain(),
                    "synthetic_receipt_fixture": true
                }),
                "failed_known" => json!({
                    "mutation": mutation,
                    "error": Error::new(
                        ErrorCode::BackendFailed,
                        "retained known observation failure fixture"
                    ),
                    "synthetic_receipt_fixture": true
                }),
                _ => panic!("unsupported fixture stage"),
            };
            service
                .append_production_receipt(ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: request_id.into(),
                    request_sha256: request_digest(command, args).unwrap(),
                    command: command.into(),
                    stage: stage.into(),
                    payload,
                })
                .unwrap();
        }

        fn append_completed(
            service: &StudioService,
            project: &Project,
            request_id: &str,
            command: &str,
            mutation: bool,
        ) {
            // Synthetic durable originals, not native execution evidence.
            let args = json!({"synthetic_retained_original": request_id});
            for stage in ["dispatching", "completed"] {
                append_record(
                    service, project, request_id, command, &args, mutation, stage,
                );
            }
        }

        fn append_observation(
            service: &StudioService,
            project: &Project,
            request_id: &str,
            case: Case,
        ) {
            let args = json!({"job_ref": "retained-observation-fixture-job"});
            append_record(
                service,
                project,
                request_id,
                case.command,
                &args,
                false,
                "dispatching",
            );
            if case.head == Head::ReadDispatching {
                return;
            }
            append_record(
                service,
                project,
                request_id,
                case.command,
                &args,
                false,
                "outcome_unknown",
            );
            let terminal = match case.head {
                Head::CompletedAfterUnknown => Some("completed"),
                Head::FailedKnownAfterUnknown => Some("failed_known"),
                Head::Unknown | Head::ReadDispatching => None,
            };
            if let Some(stage) = terminal {
                append_record(
                    service,
                    project,
                    request_id,
                    case.command,
                    &args,
                    false,
                    stage,
                );
            }
        }

        fn options_for(project: &Project) -> FilmBuildOptions {
            FilmBuildOptions {
                frame_rate: Rate::new(30, 1).unwrap(),
                font_family: crate::film::MOTION_CANVAS_FONT_FAMILY.into(),
                mono_font_family: crate::film::MOTION_CANVAS_MONO_FONT_FAMILY.into(),
                scene_intents: project
                    .scenes
                    .iter()
                    .filter(|scene| scene.renderer == RendererKind::MotionCanvas)
                    .map(|scene| crate::film::SceneFilmIntent {
                        scene_id: scene.id,
                        role: NarrativeRole::Mechanism,
                        archetype: Archetype::Statement,
                    })
                    .collect(),
            }
        }

        fn add_scene_with_existing_node(
            service: &StudioService,
            project: Project,
            name: &str,
            renderer: RendererKind,
        ) -> (Project, Uuid) {
            let mut node = project.scenes[0].nodes[0].clone();
            node.id = Uuid::now_v7();
            node.name = name.into();

            let project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("late-fixture-{name}-scene"),
                    &Change::AddScene {
                        name: name.into(),
                        objective: "Retained segment history fixture".into(),
                        duration_seconds: 2,
                    },
                )
                .unwrap()
                .project;
            let scene_id = project.scenes.last().unwrap().id;
            let project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("late-fixture-{name}-renderer"),
                    &Change::SetSceneRenderer { scene_id, renderer },
                )
                .unwrap()
                .project;
            let project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("late-fixture-{name}-node"),
                    &Change::AddCanvasNode { scene_id, node },
                )
                .unwrap()
                .project;
            (project, scene_id)
        }

        async fn check(route: Route, index: usize, case: Case, unresolved: bool) {
            let (service, initial, temp) = fixture_service();
            let mut project = motion_canvas_fixture(&service, initial);
            let mut selected_scene = project.scenes[0].id;
            let deliverable = project.deliverables[0].id;
            let parent = format!("late-boundary-{route:?}-{index}");

            if route == Route::Manim {
                project = service
                    .apply(
                        project.id,
                        &project.stamp(),
                        "late-fixture-manim-renderer",
                        &Change::SetSceneRenderer {
                            scene_id: selected_scene,
                            renderer: RendererKind::ManimCommunity,
                        },
                    )
                    .unwrap()
                    .project;
            }

            let mut gap_scene = None;
            if case.origin == Origin::OldRun {
                assert!(matches!(route, Route::Full | Route::Window));
                let (next, gap) =
                    add_scene_with_existing_node(&service, project, "gap", RendererKind::Blender);
                let (next, tail) = add_scene_with_existing_node(
                    &service,
                    next,
                    "tail",
                    RendererKind::MotionCanvas,
                );
                project = next;
                gap_scene = Some(gap);
                selected_scene = tail;

                let original_plan = crate::film::plan_motion_canvas_segments(
                    &project,
                    deliverable,
                    &options_for(&project),
                )
                .unwrap();
                assert_eq!(original_plan.len(), 2);
                assert_eq!(original_plan[1].scene_ids, vec![tail]);
            }

            let original = project.clone();
            let original_parent = if case.origin == Origin::Neighbor {
                format!("{parent}-neighbor")
            } else {
                parent.clone()
            };
            let original_run = usize::from(case.origin == Origin::OldRun);
            let original_prefix = match route {
                Route::Full | Route::Window => {
                    format!("{original_parent}:motion:{original_run}")
                }
                Route::Manim | Route::Hyperframes => original_parent,
            };
            let current_prefix = match route {
                Route::Full | Route::Window => format!("{parent}:motion:0"),
                Route::Manim | Route::Hyperframes => parent.clone(),
            };
            let (start_suffix, start_command) = match route {
                Route::Manim => ("start", "driver.manim-community.static.render.start"),
                Route::Full | Route::Window => {
                    ("render-start", "driver.motion-canvas.render.start")
                }
                Route::Hyperframes => ("render-start", "driver.hyperframes.render.start"),
            };

            // A neighboring observation must not hide this operation's Completed start.
            // For an old run, retain its actual old run index instead.
            let start_prefix = if case.origin == Origin::OldRun {
                &original_prefix
            } else {
                &current_prefix
            };
            append_completed(
                &service,
                &original,
                &format!("{start_prefix}:{start_suffix}"),
                start_command,
                true,
            );

            let observation_id = format!("{original_prefix}:{}", case.suffix);
            append_observation(&service, &original, &observation_id, case);

            if case.padding {
                // More than 256 DISTINCT newer heads, not merely 256 repeated rows.
                for foreign in 0..270 {
                    append_completed(
                        &service,
                        &original,
                        &format!("{parent}-foreign-{foreign}:inspect"),
                        "driver.motion-canvas.composition.inspect",
                        false,
                    );
                }
                let suffix = service.production_receipts(project.id, 256).unwrap();
                assert!(
                    suffix
                        .iter()
                        .all(|receipt| receipt.request_id != observation_id),
                    "the fixture must put the relevant head outside the old suffix"
                );
            }

            if let Some(gap) = gap_scene {
                project = service
                    .apply(
                        project.id,
                        &project.stamp(),
                        "late-fixture-collapse-original-runs",
                        &Change::SetSceneRenderer {
                            scene_id: gap,
                            renderer: RendererKind::MotionCanvas,
                        },
                    )
                    .unwrap()
                    .project;
                let current_plan = crate::film::plan_motion_canvas_segments(
                    &project,
                    deliverable,
                    &options_for(&project),
                )
                .unwrap();
                assert_eq!(current_plan.len(), 1);
                assert!(current_plan[0].scene_ids.contains(&selected_scene));
            }

            // Window source bounds are GLOBAL timeline frames, not scene-relative.
            // Three two-second scenes put the old tail at exactly 4s / frame 120.
            let source_window = if case.origin == Origin::OldRun {
                let selected = project
                    .scenes
                    .iter()
                    .find(|scene| scene.id == selected_scene)
                    .unwrap();
                assert_eq!(
                    selected.start,
                    motionwright_domain::RationalTime { num: 4, den: 1 }
                );
                motionwright_domain::SourceFrameWindow {
                    start: 130,
                    end: 140,
                }
            } else {
                motionwright_domain::SourceFrameWindow { start: 10, end: 20 }
            };

            // The historical receipt precedes the declaration, and invocation uses
            // the actual CURRENT stamp. Stale-reference rejection cannot pass this test.
            project = persist_native_boundary_test_draft(&service, project);
            assert!(project.revision > original.revision);
            assert_eq!(project.generation, original.generation);
            let expected = project.stamp();
            let options = options_for(&project);
            let before = complete_snapshot(&service, project.id);

            let (connection, counter) =
                fake_transport_connection(&temp, project.resource_key(), "", "", 91);
            fs::write(&counter, b"").unwrap();
            let output_root = connection.output_root.clone();
            fs::write(output_root.join("retained-original.bin"), b"unchanged").unwrap();
            let files_before = output_files(&output_root);

            let mut coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let hook_calls = Arc::new(AtomicUsize::new(0));
            let hook_counter = Arc::clone(&hook_calls);
            coordinator.before_dispatch_admission = Some(Arc::new(move || {
                hook_counter.fetch_add(1, Ordering::SeqCst);
            }));
            let held = saturate_native_boundary_test_custody(&coordinator);
            let custody_before = coordinator.receipt_custody.snapshot();

            let result: NativeResult<()> = match route {
                Route::Manim => coordinator
                    .realize_manim_static_scene(
                        project.id,
                        &expected,
                        &parent,
                        selected_scene,
                        deliverable,
                    )
                    .await
                    .map(|_| ()),
                Route::Full => coordinator
                    .render_motion_canvas_segments(
                        project.id,
                        &expected,
                        &parent,
                        deliverable,
                        &options,
                    )
                    .await
                    .map(|_| ()),
                Route::Window => coordinator
                    .render_motion_canvas_window(
                        project.id,
                        &expected,
                        &parent,
                        &MotionCanvasWindowRequest {
                            deliverable_id: deliverable,
                            source_scene_id: selected_scene,
                            source_window,
                            options,
                        },
                    )
                    .await
                    .map(|_| ()),
                Route::Hyperframes => coordinator
                    .render_hyperframes_source(
                        project.id,
                        &expected,
                        &parent,
                        deliverable,
                        &"ab".repeat(32),
                    )
                    .await
                    .map(|_| ()),
            };
            let error = result.unwrap_err();
            let expected_code = if unresolved || route == Route::Window {
                ErrorCode::Conflict
            } else if route == Route::Full {
                ErrorCode::InvalidArgument
            } else {
                ErrorCode::Unsupported
            };
            assert_eq!(error.code, expected_code, "{route:?} {case:?}");
            assert_eq!(error.outcome_known, !unresolved, "{route:?} {case:?}");
            if !unresolved && route != Route::Window {
                assert!(error.message.contains("SceneBoundary"), "{error:?}");
            }

            assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
            assert_eq!(hook_calls.load(Ordering::SeqCst), 0);
            assert_eq!(fs::read(&counter).unwrap(), b"");
            assert_eq!(coordinator.receipt_custody.snapshot(), custody_before);
            assert_eq!(complete_snapshot(&service, project.id), before);
            assert_eq!(service.project(project.id).unwrap(), project);
            assert_eq!(output_files(&output_root), files_before);

            drop(held);
            drop(coordinator);
            drop(service);
            let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
            assert_eq!(complete_snapshot(&reopened, project.id), before);
            assert_eq!(reopened.project(project.id).unwrap(), project);
        }

        #[tokio::test]
        async fn manim_late_observation_history_precedes_declared_boundary() {
            let cases = [
                (
                    Case::new("result", "driver.manim-community.render.result").padded(),
                    true,
                ),
                (
                    Case::new("status:4294967295", "driver.manim-community.render.status"),
                    true,
                ),
                (
                    Case::new("result", "driver.manim-community.render.result")
                        .origin(Origin::Neighbor),
                    false,
                ),
                (
                    Case::new("status:01", "driver.manim-community.render.status"),
                    false,
                ),
                (
                    Case::new("status:4294967296", "driver.manim-community.render.status"),
                    false,
                ),
                (
                    Case::new("result", "driver.manim-community.render.result")
                        .head(Head::CompletedAfterUnknown),
                    false,
                ),
                (
                    Case::new("result", "driver.manim-community.render.result")
                        .head(Head::FailedKnownAfterUnknown),
                    false,
                ),
                (
                    Case::new("status:0", "driver.manim-community.render.status")
                        .head(Head::ReadDispatching),
                    false,
                ),
            ];
            for (index, (case, unresolved)) in cases.into_iter().enumerate() {
                check(Route::Manim, index, case, unresolved).await;
            }
        }

        #[tokio::test]
        async fn motion_full_retained_observations_survive_plan_and_head_changes() {
            let cases = [
                (
                    Case::new("render-result", "driver.motion-canvas.render.result").padded(),
                    true,
                ),
                (
                    Case::new("verify", "driver.motion-canvas.composition.verify"),
                    true,
                ),
                (
                    Case::new(
                        "render-status:4294967295",
                        "driver.motion-canvas.render.status",
                    ),
                    true,
                ),
                (
                    Case::new(
                        "font-project-inspect",
                        "driver.motion-canvas.composition.inspect",
                    ),
                    true,
                ),
                (
                    Case::new("font-readback:1", "driver.motion-canvas.asset.list"),
                    true,
                ),
                (
                    Case::new("render-result", "driver.motion-canvas.render.result")
                        .origin(Origin::OldRun),
                    true,
                ),
                (
                    Case::new("render-result", "driver.motion-canvas.render.result")
                        .head(Head::CompletedAfterUnknown),
                    false,
                ),
                (
                    Case::new("verify", "driver.motion-canvas.composition.verify")
                        .head(Head::FailedKnownAfterUnknown),
                    false,
                ),
                (
                    Case::new("font-list:1", "driver.motion-canvas.asset.list")
                        .head(Head::ReadDispatching),
                    false,
                ),
            ];
            for (index, (case, unresolved)) in cases.into_iter().enumerate() {
                check(Route::Full, index, case, unresolved).await;
            }
        }

        #[tokio::test]
        async fn motion_window_late_unknown_precedes_completed_capture_identity() {
            let cases = [
                (
                    Case::new("render-result", "driver.motion-canvas.render.result"),
                    true,
                ),
                (
                    Case::new("verify", "driver.motion-canvas.composition.verify"),
                    true,
                ),
                (
                    Case::new(
                        "render-status:4294967295",
                        "driver.motion-canvas.render.status",
                    ),
                    true,
                ),
                (
                    Case::new("font-list:1", "driver.motion-canvas.asset.list"),
                    true,
                ),
                (
                    Case::new("verify", "driver.motion-canvas.composition.verify")
                        .origin(Origin::OldRun),
                    true,
                ),
                (
                    Case::new("render-result", "driver.motion-canvas.render.result")
                        .origin(Origin::Neighbor),
                    false,
                ),
                (
                    Case::new("render-status:01", "driver.motion-canvas.render.status"),
                    false,
                ),
                (
                    Case::new(
                        "render-status:4294967296",
                        "driver.motion-canvas.render.status",
                    ),
                    false,
                ),
            ];
            for (index, (case, unresolved)) in cases.into_iter().enumerate() {
                check(Route::Window, index, case, unresolved).await;
            }
        }

        #[tokio::test]
        async fn hyperframes_late_observations_precede_declared_boundary() {
            let cases = [
                (
                    Case::new("inspect-before", "driver.hyperframes.project.inspect"),
                    true,
                ),
                (
                    Case::new("render-result", "driver.hyperframes.render.result"),
                    true,
                ),
                (
                    Case::new(
                        "render-status:4294967295",
                        "driver.hyperframes.render.status",
                    ),
                    true,
                ),
                (
                    Case::new("inspect-before", "driver.hyperframes.project.inspect")
                        .head(Head::ReadDispatching),
                    false,
                ),
            ];
            for (index, (case, unresolved)) in cases.into_iter().enumerate() {
                check(Route::Hyperframes, index, case, unresolved).await;
            }
        }

        // Synthetic persisted history regression; execution status is recorded externally.
        // Insert this function inside production::tests::late_observation_boundary_tests.
        // Reuses that module's append_record, complete_snapshot and output_files helpers.
        // The retained receipts and transport are synthetic; no Blender run is claimed.

        #[tokio::test]
        async fn blender_removed_object_history_precedes_current_plan_and_draft_guard() {
            for head in ["outcome_unknown", "completed", "failed_known"] {
                let unresolved = head == "outcome_unknown";
                let (service, initial, temp) = fixture_service();
                let mut project = motion_canvas_fixture(&service, initial);
                let scene_id = project.scenes[0].id;
                project = service
                    .apply(
                        project.id,
                        &project.stamp(),
                        "late-blender-fixture-renderer",
                        &Change::SetSceneRenderer {
                            scene_id,
                            renderer: RendererKind::Blender,
                        },
                    )
                    .unwrap()
                    .project;

                let original_text = project.scenes[0].nodes[0].clone();
                project = service
                    .apply(
                        project.id,
                        &project.stamp(),
                        "late-blender-fixture-remove-text",
                        &Change::RemoveCanvasNode {
                            scene_id,
                            node_id: original_text.id,
                        },
                    )
                    .unwrap()
                    .project;
                for index in 0..2 {
                    let mut rectangle = original_text.clone();
                    rectangle.id = Uuid::now_v7();
                    rectangle.name = format!("Retained Blender rectangle {index}");
                    rectangle.kind = "rectangle".into();
                    rectangle.text = None;
                    rectangle.parent_id = None;
                    rectangle.rotation_deg = 0.0;
                    rectangle.opacity = 1.0;
                    rectangle.relations.clear();
                    rectangle.keyframes.clear();
                    rectangle.property_locks.clear();
                    rectangle.style.stroke = None;
                    rectangle.style.stroke_width = 0.0;
                    rectangle.style.font_family = None;
                    rectangle.style.font_size = None;
                    rectangle.style.font_weight = None;
                    rectangle.style.line_height = None;
                    rectangle.style.blend_mode = BlendMode::Normal;
                    project = service
                        .apply(
                            project.id,
                            &project.stamp(),
                            &format!("late-blender-fixture-add-{index}"),
                            &Change::AddCanvasNode {
                                scene_id,
                                node: rectangle,
                            },
                        )
                        .unwrap()
                        .project;
                }

                let original_plan = build_blender_contribution(&project, scene_id).unwrap();
                assert_eq!(original_plan.objects.len(), 2);
                let removed = &original_plan.objects[1];
                let removed_node_id = removed.node_id;
                let original_revision = project.revision;
                let base = format!("late-blender-removed-{head}");
                let request_id = format!("{base}:object:1:create");
                let command = "driver.blender.object.create";
                // These are the original builder's arguments, without invented Driver refs.
                let args = json!({
                    "name": &removed.name,
                    "primitive": removed.primitive.as_driver_name(),
                    "location": removed.location
                });
                append_record(
                    &service,
                    &project,
                    &request_id,
                    command,
                    &args,
                    true,
                    "dispatching",
                );
                append_record(
                    &service,
                    &project,
                    &request_id,
                    command,
                    &args,
                    true,
                    "outcome_unknown",
                );
                if !unresolved {
                    // A later retained known head must suppress this identity's older UNKNOWN.
                    append_record(&service, &project, &request_id, command, &args, true, head);
                }

                project = service
                    .apply(
                        project.id,
                        &project.stamp(),
                        "late-blender-remove-original-second-object",
                        &Change::RemoveCanvasNode {
                            scene_id,
                            node_id: removed_node_id,
                        },
                    )
                    .unwrap()
                    .project;
                let current_plan = build_blender_contribution(&project, scene_id).unwrap();
                assert_eq!(current_plan.objects.len(), 1);
                assert!(
                    current_plan
                        .objects
                        .iter()
                        .all(|object| object.node_id != removed_node_id)
                );
                project = persist_native_boundary_test_draft(&service, project);
                assert!(project.revision > original_revision);
                let expected = project.stamp();

                // Push the pertinent original beyond a 256-row newest-only read.
                // These completed foreign heads are explicit synthetic receipt fixtures.
                for index in 0..270 {
                    append_completed(
                        &service,
                        &project,
                        &format!("unrelated-blender-{head}-{index}"),
                        "driver.blender.introspect.summary",
                        false,
                    );
                }
                let before = complete_snapshot(&service, project.id);
                let original_head_position = before
                    .1
                    .iter()
                    .position(|receipt| receipt.request_id == request_id)
                    .unwrap();
                assert!(original_head_position >= 270);
                assert_eq!(before.1[original_head_position].stage, head);
                assert_eq!(before.1[original_head_position].revision, original_revision);
                assert!(before.1.iter().any(|receipt| {
                    receipt.request_id == request_id && receipt.stage == "outcome_unknown"
                }));

                let (connection, counter) =
                    fake_transport_connection(&temp, project.resource_key(), "", "", 91);
                fs::write(&counter, b"").unwrap();
                let output_root = connection.output_root.clone();
                fs::write(
                    output_root.join("retained-blender-history.bin"),
                    b"original synthetic output must remain unchanged",
                )
                .unwrap();
                let files_before = output_files(&output_root);
                let mut coordinator =
                    ProductionCoordinator::new(service.clone(), connection).unwrap();
                let hook_calls = Arc::new(AtomicUsize::new(0));
                let hook_counter = Arc::clone(&hook_calls);
                coordinator.before_dispatch_admission = Some(Arc::new(move || {
                    hook_counter.fetch_add(1, Ordering::SeqCst);
                }));
                let held = saturate_native_boundary_test_custody(&coordinator);
                let custody_before = coordinator.receipt_custody.snapshot();
                let error = coordinator
                    .realize_blender_scene(project.id, &expected, &base, scene_id)
                    .await
                    .unwrap_err();
                if unresolved {
                    assert_eq!(error.code, ErrorCode::Conflict, "{head}");
                    assert!(!error.outcome_known, "{head}");
                    assert!(
                        error.message.contains("unresolved original receipt"),
                        "{head}"
                    );
                } else {
                    assert_eq!(error.code, ErrorCode::Unsupported, "{head}");
                    assert!(error.outcome_known, "{head}");
                    assert!(error.message.contains("actual Film options"), "{head}");
                }
                assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
                assert_eq!(hook_calls.load(Ordering::SeqCst), 0);
                assert_eq!(fs::read(&counter).unwrap(), b"");
                assert_eq!(complete_snapshot(&service, project.id), before);
                assert_eq!(coordinator.receipt_custody.snapshot(), custody_before);
                assert_eq!(service.project(project.id).unwrap(), project);
                assert_eq!(output_files(&output_root), files_before);
                drop(held);
                drop(coordinator);
                drop(service);

                let reopened =
                    StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
                assert_eq!(complete_snapshot(&reopened, project.id), before);
                assert_eq!(reopened.project(project.id).unwrap(), project);
            }
        }

        #[cfg(unix)]
        #[tokio::test]
        async fn observed_unknown_survives_unrelated_receipt_drift() {
            use std::cell::Cell;

            // The two unresolved cases would previously reach either the final
            // watermark (one row) or the next page (257 rows) after observing UNKNOWN.
            // Resolved heads must still suppress their older UNKNOWN, without turning
            // a subsequent snapshot conflict into an uncertain original outcome.
            let cases = [
                (0_usize, None, true),
                (256, None, true),
                (0, Some("completed"), false),
                (0, Some("completed"), true),
                (0, Some("failed_known"), false),
                (0, Some("failed_known"), true),
            ];
            for (filler_count, resolved_stage, drift) in cases {
                let (service, project, temp) = fixture_service();
                let database = temp.path().join("motionwright.sqlite3");
                let peer = StudioService::open(&database).unwrap();
                assert_eq!(peer.project(project.id).unwrap(), project);

                let base = "observed-unknown-drift";
                let original_id = format!("{base}:result");
                let peer_id = format!("{base}-unrelated:result");
                let command = "driver.manim-community.render.result";
                let args = json!({"fixture": "retained-original-observation"});
                for index in 0..filler_count {
                    append_record(
                        &service,
                        &project,
                        &format!("{base}-neighbor-{index}:result"),
                        command,
                        &args,
                        false,
                        "completed",
                    );
                }
                append_record(
                    &service,
                    &project,
                    &original_id,
                    command,
                    &args,
                    false,
                    "outcome_unknown",
                );
                if let Some(stage) = resolved_stage {
                    append_record(
                        &service,
                        &project,
                        &original_id,
                        command,
                        &args,
                        false,
                        stage,
                    );
                }
                let before = complete_snapshot(&service, project.id);
                let journal = service.history(project.id, 0, 1024).unwrap();
                assert_eq!(before.0.count > 256, filler_count == 256);
                let page_calls = Cell::new(0_usize);
                let final_watermark_calls = Cell::new(0_usize);
                let result = require_no_unresolved_scene_history(
                    base,
                    SceneRequestFamily::Manim,
                    &before.0,
                    |cursor| {
                        page_calls.set(page_calls.get() + 1);
                        // This actual Service call authenticates the page with the
                        // captured watermark both before and after its SQLite read.
                        let page = service.production_receipts_snapshot_page(
                            project.id, &before.0, cursor, 256,
                        )?;
                        if page_calls.get() == 1 {
                            let head = page
                                .iter()
                                .find(|receipt| receipt.request_id == original_id)
                                .unwrap();
                            assert_eq!(head.stage, resolved_stage.unwrap_or("outcome_unknown"));
                            if drift {
                                // The independent Store appends only after the page is
                                // authenticated. It does not settle the original key.
                                append_record(
                                    &peer,
                                    &project,
                                    &peer_id,
                                    command,
                                    &args,
                                    false,
                                    "completed",
                                );
                            }
                        }
                        Ok(page)
                    },
                    || {
                        final_watermark_calls.set(final_watermark_calls.get() + 1);
                        service.production_receipt_watermark(project.id)
                    },
                );
                if resolved_stage.is_none() {
                    let error = result.unwrap_err();
                    assert_eq!(error.code, ErrorCode::Conflict);
                    assert!(!error.outcome_known);
                    assert_eq!(page_calls.get(), 1);
                    assert_eq!(final_watermark_calls.get(), 0);
                } else {
                    assert_eq!(page_calls.get(), 1);
                    assert_eq!(final_watermark_calls.get(), 1);
                    if drift {
                        let error = result.unwrap_err();
                        assert_eq!(error.code, ErrorCode::Conflict);
                        assert!(error.outcome_known);
                    } else {
                        result.unwrap();
                    }
                }

                let after = complete_snapshot(&service, project.id);
                if drift {
                    assert_eq!(after.0.count, before.0.count + 1);
                    let unrelated = after
                        .1
                        .iter()
                        .filter(|receipt| receipt.request_id == peer_id)
                        .collect::<Vec<_>>();
                    assert_eq!(unrelated.len(), 1);
                    assert_eq!(after.0.latest_id, Some(unrelated[0].id));
                    let retained = after
                        .1
                        .iter()
                        .filter(|receipt| receipt.request_id != peer_id)
                        .cloned()
                        .collect::<Vec<_>>();
                    assert_eq!(retained, before.1);
                } else {
                    assert_eq!(after, before);
                }
                assert_eq!(
                    service
                        .latest_production_receipt(project.id, &original_id)
                        .unwrap()
                        .unwrap()
                        .stage,
                    resolved_stage.unwrap_or("outcome_unknown")
                );
                assert_eq!(service.project(project.id).unwrap(), project);
                assert_eq!(service.history(project.id, 0, 1024).unwrap(), journal);
                drop(peer);
                drop(service);
                let reopened = StudioService::open(&database).unwrap();
                assert_eq!(complete_snapshot(&reopened, project.id), after);
                assert_eq!(reopened.project(project.id).unwrap(), project);
                assert_eq!(reopened.history(project.id, 0, 1024).unwrap(), journal);
            }
        }
    }
}
