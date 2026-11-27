//! Source-bound, read-only OTIO cut inspection for the installed desktop.
//! Desktop runs the embedded OTIO bridge with a protected Python and ffprobe installation.
//! The bridge does not import project media or approve a cut; only the
//! ordinary human-reviewed StudioService Change can modify the project.
use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::{OtioEditPreview, Project};
use motionwright_service::{
    OtioBridgeOperation, ProductionReceipt, ProductionReceiptInput, StorageError, StudioService,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    ffi::OsString,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::Duration,
};
use uuid::Uuid;

const BRIDGE: &[u8] = include_bytes!("../../../../tooling/otio-media/bridge.py");
const AUDIO: &[u8] =
    include_bytes!("../../../../tooling/otio-media/kdenlive_firstparty_audio_track.py");
const INTAKE: &[u8] = include_bytes!("../../../../tooling/mlt_official_runtime_intake.py");
// Real ffprobe readback is imported lazily by bridge._probe_output.
// Mocked source-unit tests do not exercise this dependency. The installed
// isolated Python runner must stage this original first-party executor, not
// attempt to resolve it through the owner's project or site-packages.
const READBACK: &[u8] = include_bytes!("../../../../tooling/manim_native_readback.py");
// -I/-S remove project and site imports. Admit only the two first-party
// module folders staged and SHA-checked above, never the owner's bundle.
const RUNNER: &[u8] = br#"import os,runpy,sys
root=sys.argv.pop(1)
if root:
    root=os.path.normcase(os.path.realpath(root))
    for entry in (sys.executable,*sys.path):
        if not entry or os.path.commonpath((root,os.path.normcase(os.path.realpath(entry))))!=root:
            raise RuntimeError('Python runtime search path leaves the protected installation')
source=sys.argv.pop(1)
source_dir=os.path.dirname(source)
sys.path.insert(0,os.path.dirname(source_dir))
sys.path.insert(0,source_dir)
runpy.run_path(source,run_name='__main__')
"#;
const MAX_PROJECT: u64 = 2 * 1024 * 1024;
const MAX_PREVIEW: u64 = 128_000;
const DEADLINE: Duration = Duration::from_secs(70);

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OtioMediaPreviewRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub bundle_path: String,
    pub edited_file: String,
    /// Original, unedited Kdenlive OTIO export in the same owner bundle.
    /// None retains the metadata-preserving OTIO inspection contract.
    #[serde(default)]
    pub received_file: Option<String>,
    pub effect_grant: Uuid,
}

#[derive(Debug, Serialize)]
pub struct OtioMediaPreviewResponse {
    pub preview: OtioEditPreview,
    pub source_project_sha256: String,
    pub media_refs_verified: usize,
    pub input_profile: &'static str,
    pub received_otio_sha256: Option<String>,
    pub edited_otio_sha256: String,
    pub runtime: &'static str,
    pub source_media_relinked: bool,
    pub editor_launched: bool,
    pub project_modified: bool,
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn refused() -> String {
    "The OTIO original or inspected media did not meet the bounded, source-exact bridge contract. Nothing was imported or saved.".into()
}

fn valid_edited_name(value: &str) -> bool {
    value.len() >= 6
        && value.len() <= 160
        && value.ends_with(".otio")
        && value != ".."
        && !value.starts_with('.')
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn link_or_reparse(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    metadata.file_type().is_symlink()
}

#[cfg(windows)]
fn same_local_windows_path(input: &Path, canonical: &Path) -> bool {
    use std::path::{Component, Prefix};
    fn drive(component: Option<Component<'_>>) -> Option<u8> {
        match component {
            Some(Component::Prefix(prefix)) => match prefix.kind() {
                Prefix::Disk(drive) | Prefix::VerbatimDisk(drive) => Some(drive),
                _ => None,
            },
            _ => None,
        }
    }
    let mut input = input.components();
    let mut canonical = canonical.components();
    matches!((drive(input.next()), drive(canonical.next())),
        (Some(a), Some(b)) if a.eq_ignore_ascii_case(&b))
        && input.eq(canonical)
}

fn canonical_plain_path(path: &Path) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err(refused());
    }
    let ancestors: Vec<_> = path.ancestors().collect();
    for ancestor in ancestors.into_iter().rev() {
        if link_or_reparse(&ancestor.symlink_metadata().map_err(|_| refused())?) {
            return Err(refused());
        }
    }
    let canonical = fs::canonicalize(path).map_err(|_| refused())?;
    #[cfg(windows)]
    let same_path = same_local_windows_path(path, &canonical) && windows_plain_components(path);
    #[cfg(not(windows))]
    let same_path = canonical == path;
    if !same_path {
        return Err(refused());
    }
    Ok(canonical)
}

#[cfg(windows)]
fn windows_plain_components(path: &Path) -> bool {
    use std::path::Component;
    path.components().all(|component| match component {
        Component::Normal(name) => name
            .to_str()
            .is_some_and(|name| !name.contains(':') && !name.ends_with(['.', ' '])),
        Component::ParentDir | Component::CurDir => false,
        _ => true,
    })
}

pub(super) fn canonical_owner_directory(path: &str) -> Result<PathBuf, String> {
    if path.trim() != path
        || path.is_empty()
        || path.len() > 1800
        || path.chars().any(char::is_control)
    {
        return Err("Choose one absolute, bounded original OTIO bundle folder.".into());
    }
    let input = PathBuf::from(path);
    let canonical = canonical_plain_path(&input)?;
    if !canonical.is_dir() {
        return Err("Choose an existing, non-symlink original OTIO bundle.".into());
    }
    Ok(canonical)
}

fn same_regular_source(before: &fs::Metadata, after: &fs::Metadata) -> bool {
    if !before.is_file()
        || !after.is_file()
        || link_or_reparse(before)
        || link_or_reparse(after)
        || before.len() != after.len()
        || before.modified().ok() != after.modified().ok()
    {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if before.dev() != after.dev()
            || before.ino() != after.ino()
            || before.ctime() != after.ctime()
            || before.ctime_nsec() != after.ctime_nsec()
        {
            return false;
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if before.creation_time() != after.creation_time()
            || before.file_attributes() != after.file_attributes()
        {
            return false;
        }
    }
    true
}

/// Open the admitted leaf without following links or waiting for a FIFO writer.
#[cfg(not(windows))]
fn open_bridge_source(path: &Path) -> std::io::Result<fs::File> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC);
    }
    options.open(path)
}

fn read_regular_bounded(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let before_name = path.symlink_metadata().map_err(|_| refused())?;
    if !before_name.is_file()
        || link_or_reparse(&before_name)
        || before_name.len() == 0
        || before_name.len() > limit
    {
        return Err(refused());
    }
    #[cfg(windows)]
    let (initial, identity) =
        motionwright_service::open_portable_source(path, limit).map_err(|_| refused())?;
    // Read the same held regular handle, bounded even if a source file grows.
    // Re-observe the descriptor and original pathname after the read so a
    // same-sized replacement is not mistaken for the original file.
    #[cfg(test)]
    tests::before_source_open(path);
    #[cfg(not(windows))]
    let mut file = open_bridge_source(path).map_err(|_| refused())?;
    #[cfg(windows)]
    let mut file = {
        let (current, current_identity) =
            motionwright_service::open_portable_source(path, limit).map_err(|_| refused())?;
        if identity != current_identity
            || !same_regular_source(&before_name, &initial.metadata().map_err(|_| refused())?)
        {
            return Err(refused());
        }
        current
    };
    let before_fd = file.metadata().map_err(|_| refused())?;
    if !same_regular_source(&before_name, &before_fd) {
        return Err(refused());
    }
    let mut bytes = Vec::with_capacity(before_name.len() as usize);
    (&mut file)
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| refused())?;
    let after_fd = file.metadata().map_err(|_| refused())?;
    let after_name = path.symlink_metadata().map_err(|_| refused())?;
    #[cfg(windows)]
    {
        let (current, current_identity) =
            motionwright_service::open_portable_source(path, limit).map_err(|_| refused())?;
        if current_identity != identity
            || !same_regular_source(&after_fd, &current.metadata().map_err(|_| refused())?)
        {
            return Err(refused());
        }
    }
    if bytes.is_empty()
        || bytes.len() as u64 != before_name.len()
        || bytes.len() as u64 > limit
        || !same_regular_source(&before_fd, &after_fd)
        || !same_regular_source(&before_name, &after_name)
        || !same_regular_source(&after_fd, &after_name)
    {
        return Err(refused());
    }
    Ok(bytes)
}

fn write_exact(dir: &Path, name: &str, bytes: &[u8]) -> Result<PathBuf, String> {
    let target = dir.join(name);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&target)
        .map_err(|_| "The isolated OTIO bridge source could not be staged.".to_string())?;
    file.write_all(bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| "The isolated OTIO bridge source could not be finalized.".to_string())?;
    if sha(&read_regular_bounded(&target, bytes.len() as u64)?) != sha(bytes) {
        return Err("A staged OTIO bridge module differs from its compiled original.".into());
    }
    Ok(target)
}

#[cfg(target_os = "macos")]
#[path = "otio_bridge_macos.rs"]
mod macos_runtime;
#[cfg(windows)]
#[path = "otio_bridge_windows_process.rs"]
mod windows_process;
#[cfg(windows)]
#[path = "otio_bridge_windows_runtime.rs"]
mod windows_runtime;

#[derive(Debug)]
struct BridgeRuntime {
    python: PathBuf,
    ffprobe: PathBuf,
    managed_root: Option<PathBuf>,
    #[cfg(windows)]
    system_root: PathBuf,
    #[cfg(windows)]
    _windows_admission: windows_runtime::WindowsOtioRuntime,
}

const RUNTIME_DESCRIPTION: &str = if cfg!(target_os = "linux") {
    "system-root-owned-linux-python3-ffprobe; original first-party bridge source embedded"
} else if cfg!(target_os = "macos") {
    "administrator-managed-macos-python3-ffprobe; original first-party bridge source embedded"
} else {
    "administrator-managed-windows-python3-ffprobe; original first-party bridge source embedded"
};

fn fixed_runtime() -> Result<BridgeRuntime, String> {
    #[cfg(test)]
    if tests::RUNTIME_UNAVAILABLE.get() {
        return Err("The configured OTIO runtime is unavailable.".into());
    }
    platform_runtime()
}

#[cfg(target_os = "linux")]
fn platform_runtime() -> Result<BridgeRuntime, String> {
    use std::os::unix::fs::MetadataExt;
    fn root_owned(executable: &str) -> Result<PathBuf, String> {
        let path = Path::new(executable);
        let target = path.canonicalize().map_err(|_| {
            "An installed root-owned Python 3 and ffprobe runtime is required for OTIO media verification.".to_string()
        })?;
        if !matches!(target.parent(), Some(parent) if parent == Path::new("/usr/bin")
            || parent == Path::new("/bin"))
        {
            return Err(
                "OTIO verification cannot launch an executable from a user-controlled directory."
                    .into(),
            );
        }
        let meta = target.metadata().map_err(|_| refused())?;
        let directory = target.parent().ok_or_else(refused)?;
        let parent_meta = directory.metadata().map_err(|_| refused())?;
        if !meta.is_file()
            || meta.uid() != 0
            || meta.mode() & 0o022 != 0
            || meta.mode() & 0o111 == 0
            || !parent_meta.is_dir()
            || parent_meta.uid() != 0
            || parent_meta.mode() & 0o022 != 0
        {
            return Err(
                "The installed OTIO verification tools and their system directory are not protected."
                    .into(),
            );
        }
        Ok(target)
    }
    Ok(BridgeRuntime {
        python: root_owned("/usr/bin/python3")?,
        ffprobe: root_owned("/usr/bin/ffprobe")?,
        managed_root: None,
    })
}

#[cfg(target_os = "macos")]
fn platform_runtime() -> Result<BridgeRuntime, String> {
    let (python, ffprobe, root) = macos_runtime::admit()?;
    Ok(BridgeRuntime {
        python,
        ffprobe,
        managed_root: Some(root),
    })
}

#[cfg(windows)]
fn platform_runtime() -> Result<BridgeRuntime, String> {
    let admitted = windows_runtime::trusted_windows_otio_runtime().map_err(str::to_owned)?;
    Ok(BridgeRuntime {
        python: admitted.python.clone(),
        ffprobe: admitted.ffprobe.clone(),
        managed_root: Some(admitted.cwd.clone()),
        system_root: admitted.system_root.clone(),
        _windows_admission: admitted,
    })
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn platform_runtime() -> Result<BridgeRuntime, String> {
    Err("This installation has no admitted Python 3 and ffprobe runtime for OTIO media verification.".into())
}

pub(super) fn check_runtime_files() -> Result<(), String> {
    admit_runtime_before_dispatch().map(drop)
}

fn admit_runtime_before_dispatch() -> Result<BridgeRuntime, String> {
    fixed_runtime().map_err(|error| format!("OTIO bridge preflight unavailable: {error}"))
}

fn stage_bridge_sources(temp: &Path) -> Result<(PathBuf, PathBuf), String> {
    let tooling = temp.join("tooling");
    let source_dir = tooling.join("otio-media");
    fs::create_dir_all(&source_dir).map_err(|_| refused())?;
    let bridge = write_exact(&source_dir, "bridge.py", BRIDGE)?;
    write_exact(&source_dir, "kdenlive_firstparty_audio_track.py", AUDIO)?;
    write_exact(&tooling, "mlt_official_runtime_intake.py", INTAKE)?;
    write_exact(&tooling, "manim_native_readback.py", READBACK)?;
    write_exact(&source_dir, "run-original-bridge.py", RUNNER)?;
    Ok((source_dir, bridge))
}

const UNCERTAIN_BRIDGE_EXIT: &str = "The OTIO process-tree cleanup could not be proven. Do not repeat the operation while its outcome is uncertain.";

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum OtioBridgeError {
    Message(String),
    Unsettled(OtioBridgeUnsettled),
}

#[derive(Debug, Serialize)]
pub struct OtioBridgeUnsettled {
    schema: &'static str,
    certainty: &'static str,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    operation: OtioBridgeOperation,
    attempt_id: Uuid,
    workspace_name: String,
    message: &'static str,
}

impl From<String> for OtioBridgeError {
    fn from(message: String) -> Self {
        Self::Message(message)
    }
}

impl From<&str> for OtioBridgeError {
    fn from(message: &str) -> Self {
        Self::Message(message.to_owned())
    }
}

impl From<OtioBridgeError> for String {
    fn from(error: OtioBridgeError) -> Self {
        match error {
            OtioBridgeError::Message(message) => message,
            OtioBridgeError::Unsettled(original) => original.message.into(),
        }
    }
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct BridgeWorkspace {
    attempt_id: Uuid,
    workspace_name: String,
}

fn workspace_prefix(operation: OtioBridgeOperation) -> &'static str {
    match operation {
        OtioBridgeOperation::Preview => ".motionwright-otio-preview-",
        OtioBridgeOperation::Export => ".motionwright-otio-export-",
    }
}

struct BridgeTempWorkspace {
    #[cfg(any(windows, unix))]
    _working: fs::File,
    temp: tempfile::TempDir,
    #[cfg(windows)]
    private: windows_runtime::WindowsOtioWorkspace,
    #[cfg(unix)]
    _ancestors: Vec<fs::File>,
}

const WORKSPACE_REFUSED: &str = "OTIO needs a private workspace under a protected local folder.";

struct BridgeWorkspaceParent {
    #[cfg(windows)]
    windows: windows_runtime::WindowsOtioWorkspaceParent,
    #[cfg(unix)]
    path: PathBuf,
    #[cfg(unix)]
    ancestors: Vec<fs::File>,
}

impl BridgeWorkspaceParent {
    /// Check only directory metadata. Keep the admitted ancestors alive until
    /// the authorized operation has finished with its private workspace.
    fn admit(parent: &Path) -> Result<Self, String> {
        if !parent.is_absolute() || parent.ancestors().take(65).count() > 64 {
            return Err(WORKSPACE_REFUSED.into());
        }
        #[cfg(windows)]
        {
            let windows = windows_runtime::WindowsOtioWorkspaceParent::admit(parent)
                .map_err(str::to_owned)?;
            Ok(Self { windows })
        }
        #[cfg(unix)]
        {
            let mut ancestors = Vec::new();
            for path in parent.ancestors().collect::<Vec<_>>().into_iter().rev() {
                ancestors.push(open_private_unix_path(path, UnixWorkspaceEntry::Ancestor)?);
            }
            Ok(Self {
                path: parent.to_path_buf(),
                ancestors,
            })
        }
        #[cfg(not(any(windows, unix)))]
        Err(WORKSPACE_REFUSED.into())
    }

    fn create(self, operation: OtioBridgeOperation) -> Result<BridgeTempWorkspace, String> {
        #[cfg(windows)]
        {
            let name = format!("{}{}", workspace_prefix(operation), Uuid::now_v7().simple());
            let private = self.windows.create(&name).map_err(str::to_owned)?;
            let temp = tempfile::Builder::new()
                .prefix("files-")
                .tempdir_in(private.path())
                .map_err(|_| WORKSPACE_REFUSED)?;
            let working = private.hold(temp.path(), true).map_err(str::to_owned)?;
            Ok(BridgeTempWorkspace {
                _working: working,
                temp,
                private,
            })
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let temp = tempfile::Builder::new()
                .prefix(workspace_prefix(operation))
                .permissions(fs::Permissions::from_mode(0o700))
                .tempdir_in(&self.path)
                .map_err(|_| WORKSPACE_REFUSED)?;
            let working =
                open_private_unix_path(temp.path(), UnixWorkspaceEntry::PrivateDirectory)?;
            Ok(BridgeTempWorkspace {
                _working: working,
                temp,
                _ancestors: self.ancestors,
            })
        }
        #[cfg(not(any(windows, unix)))]
        {
            let _ = operation;
            Err(WORKSPACE_REFUSED.into())
        }
    }
}

impl BridgeTempWorkspace {
    fn path(&self) -> &Path {
        self.temp.path()
    }

    fn name(&self) -> Option<&str> {
        #[cfg(windows)]
        let directory = self.private.path();
        #[cfg(not(windows))]
        let directory = self.path();
        directory.file_name()?.to_str()
    }

    fn hold_sources(&self, source_dir: &Path) -> Result<Vec<fs::File>, String> {
        let tooling = source_dir.parent().ok_or(WORKSPACE_REFUSED)?;
        let originals = [
            (source_dir.join("run-original-bridge.py"), RUNNER),
            (source_dir.join("bridge.py"), BRIDGE),
            (source_dir.join("kdenlive_firstparty_audio_track.py"), AUDIO),
            (tooling.join("mlt_official_runtime_intake.py"), INTAKE),
            (tooling.join("manim_native_readback.py"), READBACK),
        ];
        #[cfg(windows)]
        {
            let mut handles = vec![
                self.private.hold(tooling, true).map_err(str::to_owned)?,
                self.private.hold(source_dir, true).map_err(str::to_owned)?,
            ];
            for (path, bytes) in originals {
                handles.push(
                    self.private
                        .hold_original(&path, bytes)
                        .map_err(str::to_owned)?,
                );
            }
            Ok(handles)
        }
        #[cfg(unix)]
        {
            let mut handles = vec![
                open_private_unix_path(tooling, UnixWorkspaceEntry::Ancestor)?,
                open_private_unix_path(source_dir, UnixWorkspaceEntry::Ancestor)?,
            ];
            for (path, expected) in originals {
                let mut file = open_private_unix_path(&path, UnixWorkspaceEntry::OriginalFile)?;
                let mut bytes = Vec::new();
                (&mut file)
                    .take(expected.len() as u64 + 1)
                    .read_to_end(&mut bytes)
                    .map_err(|_| WORKSPACE_REFUSED)?;
                if bytes != expected {
                    return Err(WORKSPACE_REFUSED.into());
                }
                handles.push(file);
            }
            Ok(handles)
        }
        #[cfg(not(any(windows, unix)))]
        {
            let _ = originals;
            Err(WORKSPACE_REFUSED.into())
        }
    }
}

#[cfg(unix)]
enum UnixWorkspaceEntry {
    Ancestor,
    PrivateDirectory,
    OriginalFile,
}

#[cfg(unix)]
fn open_private_unix_path(path: &Path, entry: UnixWorkspaceEntry) -> Result<fs::File, String> {
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    let kind = if matches!(entry, UnixWorkspaceEntry::OriginalFile) {
        0
    } else {
        libc::O_DIRECTORY
    };
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(kind | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| WORKSPACE_REFUSED)?;
    let before = file.metadata().map_err(|_| WORKSPACE_REFUSED)?;
    let current = path.symlink_metadata().map_err(|_| WORKSPACE_REFUSED)?;
    // SAFETY: reading the effective process identity has no side effects.
    let uid = unsafe { libc::geteuid() };
    // MetadataExt::mode() is u32 on both Unix targets; Darwin's mode_t is narrower.
    const STICKY_BIT: u32 = 0o1000;
    let permissions_ok = match entry {
        UnixWorkspaceEntry::Ancestor => {
            before.is_dir() && (before.mode() & 0o022 == 0 || before.mode() & STICKY_BIT != 0)
        }
        UnixWorkspaceEntry::PrivateDirectory => {
            before.is_dir() && before.uid() == uid && before.mode() & 0o077 == 0
        }
        UnixWorkspaceEntry::OriginalFile => {
            before.is_file()
                && before.nlink() == 1
                && before.uid() == uid
                && before.mode() & 0o022 == 0
        }
    };
    if !permissions_ok
        || (before.uid() != 0 && before.uid() != uid)
        || before.dev() != current.dev()
        || before.ino() != current.ino()
    {
        return Err(WORKSPACE_REFUSED.into());
    }
    #[cfg(target_os = "macos")]
    if !macos_runtime::acl_cannot_grant_access(&file) {
        return Err(WORKSPACE_REFUSED.into());
    }
    Ok(file)
}

fn valid_workspace(operation: OtioBridgeOperation, workspace_name: &str) -> bool {
    workspace_name
        .strip_prefix(workspace_prefix(operation))
        .is_some_and(|suffix| {
            !suffix.is_empty()
                && suffix.len() <= 32
                && suffix.bytes().all(|byte| byte.is_ascii_alphanumeric())
        })
}

fn unsettled_bridge_details(
    receipt: ProductionReceipt,
    operation: OtioBridgeOperation,
) -> Result<OtioBridgeUnsettled, String> {
    let metadata: BridgeWorkspace = serde_json::from_value(receipt.payload)
        .map_err(|_| "The original OTIO workspace identity could not be read.")?;
    if receipt.request_id != format!("otio-{}", metadata.attempt_id)
        || metadata.attempt_id.is_nil()
        || !valid_workspace(operation, &metadata.workspace_name)
    {
        return Err("The original OTIO workspace identity could not be verified.".into());
    }
    Ok(OtioBridgeUnsettled {
        schema: "motionwright.otio-bridge-unsettled/1",
        certainty: "outcome_unknown",
        project_id: receipt.project_id,
        generation: receipt.generation,
        revision: receipt.revision,
        operation,
        attempt_id: metadata.attempt_id,
        workspace_name: metadata.workspace_name,
        message: UNCERTAIN_BRIDGE_EXIT,
    })
}

pub fn unsettled_bridge_operations(
    service: &StudioService,
    project_id: Uuid,
    generation: Uuid,
) -> Result<Vec<OtioBridgeUnsettled>, String> {
    let mut pending = Vec::new();
    for operation in [OtioBridgeOperation::Preview, OtioBridgeOperation::Export] {
        if let Some(receipt) = service
            .unsettled_otio_bridge(project_id, generation, operation)
            .map_err(|_| "The original OTIO process state could not be checked.")?
        {
            pending.push(unsettled_bridge_details(receipt, operation)?);
        }
    }
    Ok(pending)
}

fn require_bridge_idle(
    service: &StudioService,
    project: &Project,
    operation: OtioBridgeOperation,
) -> Result<(), OtioBridgeError> {
    if let Some(receipt) = service
        .unsettled_otio_bridge(project.id, project.generation, operation)
        .map_err(|_| "The original OTIO process state could not be checked.")?
    {
        return Err(OtioBridgeError::Unsettled(unsettled_bridge_details(
            receipt, operation,
        )?));
    }
    Ok(())
}

enum BridgeExecutionFailure {
    Stopped(String),
    Unsettled,
}

impl From<String> for BridgeExecutionFailure {
    fn from(message: String) -> Self {
        Self::Stopped(message)
    }
}

impl From<&str> for BridgeExecutionFailure {
    fn from(message: &str) -> Self {
        Self::Stopped(message.to_owned())
    }
}

/// Retain the dispatch before invoking a process, and release the workspace
/// only after observing that its process tree stopped and committing that fact.
fn run_tracked_bridge(
    service: &StudioService,
    project: &Project,
    operation: OtioBridgeOperation,
    temp: &mut BridgeTempWorkspace,
    run: impl FnOnce() -> Result<(), BridgeExecutionFailure>,
) -> Result<(), OtioBridgeError> {
    let workspace_name = temp
        .name()
        .filter(|name| valid_workspace(operation, name))
        .ok_or("The isolated OTIO workspace has an invalid name.")?;
    let metadata = BridgeWorkspace {
        attempt_id: Uuid::now_v7(),
        workspace_name: workspace_name.into(),
    };
    let payload = serde_json::to_value(&metadata).map_err(|_| refused())?;
    let request_sha256 = sha(&serde_json::to_vec(&(
        project.id,
        project.generation,
        project.revision,
        operation.command(),
        &metadata,
    ))
    .map_err(|_| refused())?);
    let mut record = ProductionReceiptInput {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: format!("otio-{}", metadata.attempt_id),
        request_sha256,
        command: operation.command().into(),
        stage: "dispatching".into(),
        payload,
    };
    let original = match service.append_production_receipt(record.clone()) {
        Ok(receipt) => receipt,
        Err(StorageError::RequestReuse) => {
            require_bridge_idle(service, project, operation)?;
            return Err("The OTIO process state changed before dispatch.".into());
        }
        Err(_) => {
            return Err(
                "The OTIO dispatch could not be retained. The bridge did not start.".into(),
            );
        }
    };

    temp.temp.disable_cleanup(true);
    let outcome = run();
    let stopped = !matches!(outcome, Err(BridgeExecutionFailure::Unsettled));
    record.stage = if stopped {
        "bridge_stopped"
    } else {
        "outcome_unknown"
    }
    .into();
    if service.append_production_receipt(record).is_err() || !stopped {
        return Err(OtioBridgeError::Unsettled(unsettled_bridge_details(
            original, operation,
        )?));
    }
    temp.temp.disable_cleanup(false);
    match outcome {
        Ok(()) => Ok(()),
        Err(BridgeExecutionFailure::Stopped(message)) => Err(message.into()),
        Err(BridgeExecutionFailure::Unsettled) => Err(OtioBridgeError::Unsettled(
            unsettled_bridge_details(original, operation)?,
        )),
    }
}

fn run_bridge(
    runtime: &BridgeRuntime,
    scripts: &Path,
    bridge: &Path,
    arguments: &[OsString],
) -> Result<(), BridgeExecutionFailure> {
    #[cfg(unix)]
    {
        // Unix launches by path. Recheck filesystem protection immediately before launch.
        let current = fixed_runtime()?;
        if current.python != runtime.python
            || current.ffprobe != runtime.ffprobe
            || current.managed_root != runtime.managed_root
        {
            return Err("The installed OTIO runtime changed during preparation.".into());
        }
    }
    let stderr = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(scripts.join("original-bridge-stderr.txt"))
        .map_err(|_| refused())?;
    let workspace = scripts.parent().ok_or_else(refused)?;
    let runner = scripts.join("run-original-bridge.py");
    let managed_root = runtime.managed_root.as_deref();
    let argv: Vec<_> = ["-I", "-S", "-B"]
        .into_iter()
        .map(std::ffi::OsStr::new)
        .chain([
            runner.as_os_str(),
            managed_root.unwrap_or_else(|| Path::new("")).as_os_str(),
            bridge.as_os_str(),
        ])
        .chain(arguments.iter().map(OsString::as_os_str))
        .collect();

    #[cfg(unix)]
    {
        run_bridge_unix(runtime, &argv, workspace, stderr)
    }
    #[cfg(windows)]
    {
        use windows_process::{BridgeDirectories, BridgeProcessError};
        windows_process::run(
            &runtime.python,
            &argv,
            &runtime.ffprobe,
            BridgeDirectories {
                cwd: managed_root.ok_or_else(refused)?,
                temp: workspace,
                system_root: &runtime.system_root,
            },
            &stderr,
            DEADLINE,
        )
        .map_err(|error| match error {
            BridgeProcessError::Start(error) => format!(
                "The protected OTIO bridge process could not start ({}).",
                error.kind()
            )
            .into(),
            BridgeProcessError::Exit(code) => format!(
                "The protected OTIO bridge exited with status {code}. {}",
                refused()
            )
            .into(),
            BridgeProcessError::TimedOut => {
                "The protected OTIO media inspection timed out and the entire process tree stopped."
                    .into()
            }
            BridgeProcessError::Uncertain => BridgeExecutionFailure::Unsettled,
        })
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (runtime, argv, workspace, stderr);
        Err(refused().into())
    }
}

#[cfg(unix)]
fn run_bridge_unix(
    runtime: &BridgeRuntime,
    argv: &[&std::ffi::OsStr],
    workspace: &Path,
    stderr: fs::File,
) -> Result<(), BridgeExecutionFailure> {
    use std::{
        os::unix::process::CommandExt,
        process::{Command, Stdio},
        thread,
        time::Instant,
    };
    let started = Instant::now();
    let mut child = Command::new(&runtime.python)
        .args(argv)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("MOTIONWRIGHT_OTIO_FFPROBE", &runtime.ffprobe)
        .env("HOME", workspace)
        .env("TMPDIR", workspace)
        .env("LANG", "C.UTF-8")
        .current_dir(runtime.managed_root.as_deref().unwrap_or(Path::new("/")))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::from(stderr))
        .process_group(0)
        .spawn()
        .map_err(|_| "The protected OTIO bridge process could not start.".to_string())?;
    let group = i32::try_from(child.id())
        .ok()
        .filter(|id| *id > 1)
        .ok_or(BridgeExecutionFailure::Unsettled)?;
    loop {
        match child.try_wait() {
            Ok(Some(status)) if group_empty(group) => {
                return if status.success() {
                    Ok(())
                } else {
                    Err(refused().into())
                };
            }
            Ok(None) if started.elapsed() < DEADLINE => thread::sleep(Duration::from_millis(40)),
            Ok(None) => {
                return if stop_bridge_group(&mut child, group) {
                    Err("The protected OTIO media inspection timed out and the entire process tree stopped.".into())
                } else {
                    Err(BridgeExecutionFailure::Unsettled)
                };
            }
            _ => {
                stop_bridge_group(&mut child, group);
                return Err(BridgeExecutionFailure::Unsettled);
            }
        }
    }
}

#[cfg(unix)]
fn group_empty(group: i32) -> bool {
    // SAFETY: callers supply the positive PID of their newly created group.
    (unsafe { libc::kill(-group, 0) == -1 })
        && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

#[cfg(unix)]
fn stop_bridge_group(child: &mut std::process::Child, group: i32) -> bool {
    use std::{thread, time::Instant};
    // SAFETY: this is the dedicated group created by the current invocation.
    unsafe { libc::kill(-group, libc::SIGKILL) };
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs(5) {
        if matches!(child.try_wait(), Ok(Some(_))) && group_empty(group) {
            return true;
        }
        thread::sleep(Duration::from_millis(25));
    }
    false
}

fn preview_subject(bundle: &str, edited: &str, received: Option<&str>) -> Result<String, String> {
    if !valid_edited_name(edited)
        || received.is_some_and(|name| {
            !valid_edited_name(name)
                || name == edited
                || name == "timeline.otio"
                || edited == "timeline.otio"
        })
    {
        return Err("Choose distinct original and edited .otio filenames inside the source bundle; timeline.otio remains the immutable Motionwright export.".into());
    }
    match received {
        Some(initial) => {
            serde_json::to_string(&("otio-kdenlive-pair-preview/1", bundle, initial, edited))
        }
        None => serde_json::to_string(&("otio-bridge-preview/1", bundle, edited)),
    }
    .map_err(|_| refused())
}

/// Adapt the existing native reader's bounded proposal, never its execution
/// status. Both original files and the immutable capsule must match this
/// invocation's held-byte observations before any owner Change can be offered.
fn kdenlive_owner_preview(
    raw: &[u8],
    manifest_sha: &str,
    original_otio_sha: &str,
    received_sha: &str,
    edited_sha: &str,
    source_sha: &str,
) -> Result<OtioEditPreview, String> {
    let native: serde_json::Value = serde_json::from_slice(raw).map_err(|_| refused())?;
    for (field, expected) in [
        ("schema", "motionwright.kdenlive-bound-cut-preview/1"),
        ("profile", "kdenlive-bound-video-cut-v1"),
        ("status", "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED"),
        ("capsule_manifest_sha256", manifest_sha),
        ("original_otio_sha256", original_otio_sha),
        ("received_otio_sha256", received_sha),
        ("edited_otio_sha256", edited_sha),
        ("source_project_sha256", source_sha),
        ("nle_receiver_acceptance", "NOT_VERIFIED"),
        ("native_project_reopened", "NOT_VERIFIED"),
        ("semantic_native_roundtrip", "NOT_SUPPORTED"),
    ] {
        if native.get(field).and_then(serde_json::Value::as_str) != Some(expected) {
            return Err(refused());
        }
    }
    for (field, expected) in [
        ("native_final_frames_verified", false),
        ("edit_imported", false),
        ("source_media_relinked", false),
        ("raw_files_rewritten", false),
        ("owner_revision_commit_required", true),
        ("human_review_required", true),
        ("publication_approved", false),
    ] {
        if native.get(field).and_then(serde_json::Value::as_bool) != Some(expected) {
            return Err(refused());
        }
    }
    let original_order = native["original_scene_order"]
        .as_array()
        .ok_or_else(refused)?;
    if original_order.is_empty()
        || original_order.len() > 32
        || native["original_missing_scene_ids"] != serde_json::json!([])
        || [
            "media_refs_verified",
            "received_clip_count",
            "edited_clip_count",
        ]
        .into_iter()
        .any(|field| native[field].as_u64() != Some(original_order.len() as u64))
        || native["source_identity_mapping"].as_array().map(Vec::len) != Some(original_order.len())
    {
        return Err(refused());
    }
    let mut proposal = serde_json::Map::new();
    for field in [
        "source_project_sha256",
        "project_id",
        "generation",
        "expected_project_revision",
        "original_scene_order",
        "proposed_scene_order",
        "native_change_proposals",
        "media_refs_verified",
        "loss_report",
        "property_fidelity",
        "edit_imported",
        "source_media_relinked",
        "owner_revision_commit_required",
        "human_review_required",
        "semantic_native_roundtrip",
        "nle_receiver_acceptance",
        "publication_approved",
    ] {
        proposal.insert(field.into(), native.get(field).ok_or_else(refused)?.clone());
    }
    proposal.insert(
        "schema".into(),
        serde_json::json!("motionwright.otio-edit-preview/1"),
    );
    proposal.insert("missing_references_retained".into(), serde_json::json!(0));
    proposal.insert("rendering_performed".into(), serde_json::json!(false));
    // These immutable input identities remain in the actual reviewed Change
    // and its journal. They do not claim an authenticated NLE process.
    proposal.get_mut("loss_report").and_then(serde_json::Value::as_array_mut)
        .ok_or_else(refused)?.push(serde_json::json!(format!(
            "Original Kdenlive cut files: initial SHA-256 {received_sha}; edited SHA-256 {edited_sha}. Source-file inspection does not authenticate editor execution or visual approval."
        )));
    let value = serde_json::Value::Object(proposal);
    if serde_json::to_vec(&value).map_err(|_| refused())?.len() as u64 > MAX_PREVIEW {
        return Err(refused());
    }
    serde_json::from_value(value).map_err(|_| refused())
}

struct PreviewDocuments {
    original: Vec<u8>,
    manifest: Vec<u8>,
    edited: Vec<u8>,
    received: Option<Vec<u8>>,
}

fn read_authorized_preview_documents(
    grants: &EffectGrantRegistry,
    request: &OtioMediaPreviewRequest,
    subject: &str,
    bundle: &Path,
    project: &Project,
) -> Result<PreviewDocuments, String> {
    grants.consume(
        request.effect_grant,
        EffectKind::ImportLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        subject,
    )?;
    let original = read_regular_bounded(&bundle.join("source-project.json"), MAX_PROJECT)?;
    let source_project: Project = serde_json::from_slice(&original).map_err(|_| {
        "OTIO source-project.json must contain one complete original Motionwright Project."
            .to_string()
    })?;
    if source_project != *project {
        return Err("The original OTIO media bundle does not belong to the currently saved project revision.".into());
    }
    let manifest = read_regular_bounded(&bundle.join("bridge-manifest.json"), MAX_PROJECT)?;
    let edited = read_regular_bounded(&bundle.join(&request.edited_file), MAX_PROJECT)?;
    let received = request
        .received_file
        .as_ref()
        .map(|name| read_regular_bounded(&bundle.join(name), MAX_PROJECT))
        .transpose()?;
    if edited.len() < 16 || received.as_ref().is_some_and(|bytes| bytes.len() < 16) {
        return Err(refused());
    }
    Ok(PreviewDocuments {
        original,
        manifest,
        edited,
        received,
    })
}

/// Every input is source-scoped and the one-use local-import grant is consumed
/// before opening the original documents or starting Python/ffprobe. A complete original media bundle is required:
/// an arbitrary .otio file, pasted claim or unverified JSON is insufficient.
pub fn preview(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: OtioMediaPreviewRequest,
) -> Result<OtioMediaPreviewResponse, OtioBridgeError> {
    let subject = preview_subject(
        &request.bundle_path,
        &request.edited_file,
        request.received_file.as_deref(),
    )?;
    let runtime = admit_runtime_before_dispatch()?;
    let bundle = canonical_owner_directory(&request.bundle_path)?;
    let project = service.project(request.project_id).map_err(|_| refused())?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err(
            "The OTIO source revision changed. Refresh and export a new source bundle.".into(),
        );
    }
    let operation = OtioBridgeOperation::Preview;
    require_bridge_idle(service, &project, operation)?;
    let parent = BridgeWorkspaceParent::admit(bundle.parent().ok_or_else(refused)?)?;
    let PreviewDocuments {
        original,
        manifest,
        edited,
        received,
    } = read_authorized_preview_documents(grants, &request, &subject, &bundle, &project)?;
    let mut temp = parent.create(operation)?;
    let (source_dir, bridge) = stage_bridge_sources(temp.path())?;
    let _original_sources = temp.hold_sources(&source_dir)?;
    let snapshot = write_exact(temp.path(), "current-project.json", &original)?;
    let output = temp.path().join("review-proposal.json");
    let mut arguments = if let (Some(initial_name), Some(initial)) =
        (request.received_file.as_deref(), received.as_ref())
    {
        let native_request = serde_json::to_vec(&serde_json::json!({
            "schema": "motionwright.kdenlive-bound-cut-request/1",
            "capsule_manifest_sha256": sha(&manifest),
            "received_otio": initial_name,
            "received_sha256": sha(initial),
            "edited_otio": request.edited_file,
            "edited_sha256": sha(&edited),
        }))
        .map_err(|_| refused())?;
        let selected = write_exact(temp.path(), "native-cut-request.json", &native_request)?;
        vec![
            OsString::from("preview-kdenlive-cut"),
            OsString::from("--bundle"),
            bundle.as_os_str().to_owned(),
            OsString::from("--raw-root"),
            bundle.as_os_str().to_owned(),
            OsString::from("--request-file"),
            selected.as_os_str().to_owned(),
            // Only media under the selected bundle are authorized. Absolute
            // external paths and implicit relinking remain refused.
            OsString::from("--media-root"),
            bundle.as_os_str().to_owned(),
        ]
    } else {
        vec![
            OsString::from("preview-import"),
            OsString::from("--bundle"),
            bundle.as_os_str().to_owned(),
            OsString::from("--edited"),
            OsString::from(request.edited_file.as_str()),
        ]
    };
    arguments.extend([
        OsString::from("--current-project"),
        snapshot.as_os_str().to_owned(),
        OsString::from("--output"),
        output.as_os_str().to_owned(),
    ]);
    run_tracked_bridge(service, &project, operation, &mut temp, || {
        run_bridge(&runtime, &source_dir, &bridge, &arguments)
    })?;
    if read_regular_bounded(&bundle.join("source-project.json"), MAX_PROJECT)? != original
        || read_regular_bounded(&bundle.join("bridge-manifest.json"), MAX_PROJECT)? != manifest
        || read_regular_bounded(&bundle.join(&request.edited_file), MAX_PROJECT)? != edited
    {
        return Err("The original OTIO source or edited cut changed during one media verification. Nothing was saved.".into());
    }
    if let (Some(name), Some(initial)) = (request.received_file.as_ref(), received.as_ref())
        && read_regular_bounded(&bundle.join(name), MAX_PROJECT)? != *initial
    {
        return Err(
            "The initial Kdenlive export changed during inspection. Nothing was saved.".into(),
        );
    }
    let raw = read_regular_bounded(&output, MAX_PROJECT)?;
    let preview: OtioEditPreview = if let Some(initial) = received.as_ref() {
        let manifest_value: serde_json::Value =
            serde_json::from_slice(&manifest).map_err(|_| refused())?;
        let timeline_sha = manifest_value["otio_sha256"].as_str().ok_or_else(refused)?;
        kdenlive_owner_preview(
            &raw,
            &sha(&manifest),
            timeline_sha,
            &sha(initial),
            &sha(&edited),
            &sha(&original),
        )?
    } else {
        if raw.len() as u64 > MAX_PREVIEW {
            return Err(refused().into());
        }
        serde_json::from_slice(&raw).map_err(|_| refused())?
    };
    if preview.source_project_sha256 != sha(&original)
        || preview.project_id != project.id
        || preview.generation != project.generation
        || preview.expected_project_revision != project.revision
        || preview.rendering_performed
        || preview.source_media_relinked
        || preview.edit_imported
        || preview.publication_approved
    {
        return Err(refused().into());
    }
    preview
        .bounded_native_changes(&project)
        .map_err(|_| refused())?;
    if service.project(request.project_id).map_err(|_| refused())? != project {
        return Err("The saved Project changed while the external OTIO media bundle was being inspected. Nothing was saved.".into());
    }
    Ok(OtioMediaPreviewResponse {
        source_project_sha256: sha(&original),
        media_refs_verified: preview.media_refs_verified,
        input_profile: if received.is_some() {
            "kdenlive-otio-pair"
        } else {
            "metadata-preserving-otio"
        },
        received_otio_sha256: received.as_ref().map(|bytes| sha(bytes)),
        edited_otio_sha256: sha(&edited),
        preview,
        runtime: RUNTIME_DESCRIPTION,
        source_media_relinked: false,
        editor_launched: false,
        project_modified: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    type BeforeSourceOpen = Box<dyn FnOnce(&Path)>;
    std::thread_local! {
        pub(super) static RUNTIME_UNAVAILABLE: std::cell::Cell<bool> =
            const { std::cell::Cell::new(false) };
        static BEFORE_SOURCE_OPEN: std::cell::RefCell<Option<BeforeSourceOpen>> =
            const { std::cell::RefCell::new(None) };
    }

    pub(super) fn before_source_open(path: &Path) {
        let hook = BEFORE_SOURCE_OPEN.with(|slot| slot.borrow_mut().take());
        if let Some(hook) = hook {
            hook(path);
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn original_source_open_never_follows_a_racing_symlink() {
        let root = tempfile::tempdir().unwrap();
        let selected = root.path().join("owner-cut.otio");
        let retained = root.path().join("retained-original.otio");
        let foreign = root.path().join("foreign-cut.otio");
        fs::write(&selected, b"original cut").unwrap();
        fs::write(&foreign, b"foreign clip").unwrap();
        let selected_in_hook = selected.clone();
        let foreign_in_hook = foreign.clone();
        BEFORE_SOURCE_OPEN.with(|slot| {
            assert!(
                slot.borrow_mut()
                    .replace(Box::new(move |path| {
                        assert_eq!(path, selected_in_hook);
                        fs::rename(path, &retained).unwrap();
                        std::os::unix::fs::symlink(&foreign_in_hook, path).unwrap();
                        // Require refusal at open itself, before an unrelated file
                        // descriptor can be mistaken for the selected original.
                        let error = open_bridge_source(path).unwrap_err();
                        assert_eq!(error.raw_os_error(), Some(libc::ELOOP));
                    }))
                    .is_none()
            );
        });
        assert!(read_regular_bounded(&selected, 64).is_err());
        assert_eq!(fs::read(&foreign).unwrap(), b"foreign clip");
        assert!(
            fs::symlink_metadata(&selected)
                .unwrap()
                .file_type()
                .is_symlink()
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn original_source_open_rejects_racing_fifo_without_waiting_for_a_writer() {
        use std::os::unix::{ffi::OsStrExt, fs::OpenOptionsExt};
        use std::sync::mpsc;

        let root = tempfile::tempdir().unwrap();
        let selected = root.path().join("owner-cut.otio");
        let retained = root.path().join("retained-original.otio");
        fs::write(&selected, b"original cut").unwrap();
        let selected_in_hook = selected.clone();
        BEFORE_SOURCE_OPEN.with(|slot| {
            assert!(
                slot.borrow_mut()
                    .replace(Box::new(move |path| {
                        assert_eq!(path, selected_in_hook);
                        fs::rename(path, &retained).unwrap();
                        let name = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
                        // SAFETY: this test owns the private directory and the NUL-
                        // terminated path; only a benign local FIFO is created.
                        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
                    }))
                    .is_none()
            );
        });

        // Rescue an incorrectly blocking implementation so the negative case
        // fails instead of hanging the test worker indefinitely. The corrected
        // reader must finish without ever needing this synthetic FIFO writer.
        let (finished, wait) = mpsc::channel();
        let rescue_path = selected.clone();
        let rescue = std::thread::spawn(move || {
            if wait.recv_timeout(Duration::from_secs(2)).is_ok() {
                return false;
            }
            let _release_blocked_open = OpenOptions::new()
                .read(true)
                .write(true)
                .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW)
                .open(rescue_path)
                .unwrap();
            true
        });
        let observed = read_regular_bounded(&selected, 64);
        let _ = finished.send(());
        let required_rescue = rescue.join().unwrap();
        assert!(!required_rescue, "source open waited for a FIFO writer");
        assert!(
            observed.is_err(),
            "a FIFO cannot become original media bytes"
        );
        assert_eq!(
            fs::read(root.path().join("retained-original.otio")).unwrap(),
            b"original cut"
        );
    }

    #[test]
    fn isolated_original_bridge_stages_the_real_decoder_supervisor_source() {
        let parent = tempfile::tempdir().unwrap();
        let canonical_parent = parent.path().canonicalize().unwrap();
        let directory = BridgeWorkspaceParent::admit(&canonical_parent)
            .unwrap()
            .create(OtioBridgeOperation::Preview)
            .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                directory.path().metadata().unwrap().permissions().mode() & 0o777,
                0o700
            );
        }
        let (source_dir, bridge) = stage_bridge_sources(directory.path()).unwrap();
        let original_handles = directory.hold_sources(&source_dir).unwrap();
        #[cfg(windows)]
        {
            assert!(OpenOptions::new().write(true).open(&bridge).is_err());
            assert!(fs::rename(directory.path(), canonical_parent.join("replaced-stage")).is_err());
        }
        assert_eq!(
            read_regular_bounded(&bridge, BRIDGE.len() as u64).unwrap(),
            BRIDGE
        );
        let tooling = source_dir.parent().unwrap();
        assert_eq!(
            read_regular_bounded(
                &tooling.join("manim_native_readback.py"),
                READBACK.len() as u64,
            )
            .unwrap(),
            READBACK,
            "the existing first-party ffprobe executor is required by the real bridge; mocked file_probe unit tests cannot exercise this import"
        );
        assert_eq!(
            read_regular_bounded(
                &tooling.join("mlt_official_runtime_intake.py"),
                INTAKE.len() as u64,
            )
            .unwrap(),
            INTAKE
        );
        assert_eq!(
            read_regular_bounded(
                &source_dir.join("run-original-bridge.py"),
                RUNNER.len() as u64,
            )
            .unwrap(),
            RUNNER
        );
        drop(original_handles);
        fs::write(&bridge, b"foreign staged module").unwrap();
        assert!(directory.hold_sources(&source_dir).is_err());
    }

    #[test]
    fn refused_otio_names_and_missing_bundle_never_touch_media_or_grants() {
        for name in [
            "../edited.otio",
            "original.json",
            "/owned.otio",
            ".hidden.otio",
            "cut;bad.otio",
            "a\\b.otio",
        ] {
            assert!(!valid_edited_name(name));
        }
        assert!(valid_edited_name("edited-owner-cut.otio"));
        assert!(canonical_owner_directory("relative").is_err());
        let legacy = preview_subject("/owner", "edited.otio", None).unwrap();
        let paired = preview_subject("/owner", "edited.otio", Some("received.otio")).unwrap();
        assert_eq!(
            legacy,
            r#"["otio-bridge-preview/1","/owner","edited.otio"]"#
        );
        assert_eq!(
            paired,
            r#"["otio-kdenlive-pair-preview/1","/owner","received.otio","edited.otio"]"#
        );
        assert_ne!(
            legacy, paired,
            "a legacy grant cannot authorize another source file"
        );
        assert_ne!(
            paired,
            preview_subject("/owner", "edited.otio", Some("other.otio")).unwrap()
        );
        for name in ["", "../received.otio", "edited.otio", "timeline.otio"] {
            assert!(preview_subject("/owner", "edited.otio", Some(name)).is_err());
        }
        assert!(preview_subject("/owner", "timeline.otio", Some("received.otio")).is_err());

        let directory = tempfile::tempdir().unwrap();
        fs::write(
            directory.path().join("source-project.json"),
            b"not an original project",
        )
        .unwrap();
        let project = Project::new("Protected OTIO input").unwrap();
        let scope = EffectScope::project(project.id, project.generation, project.revision);
        let grants = EffectGrantRegistry::default();
        let bundle = directory.path().to_str().unwrap();
        let subject = preview_subject(bundle, "edited.otio", None).unwrap();
        let mut request = OtioMediaPreviewRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            bundle_path: bundle.into(),
            edited_file: "edited.otio".into(),
            received_file: None,
            effect_grant: Uuid::now_v7(),
        };
        let error = read_authorized_preview_documents(
            &grants,
            &request,
            &subject,
            directory.path(),
            &project,
        )
        .err()
        .unwrap();
        assert_eq!(
            error,
            "Effect grant is missing, expired, or outside its exact scope."
        );
        request.effect_grant = grants
            .issue(EffectKind::ImportLocal, scope, &subject)
            .unwrap()
            .token;
        let error = read_authorized_preview_documents(
            &grants,
            &request,
            &subject,
            directory.path(),
            &project,
        )
        .err()
        .unwrap();
        assert!(error.contains("must contain one complete original Motionwright Project"));
        assert!(
            grants
                .consume(
                    request.effect_grant,
                    EffectKind::ImportLocal,
                    scope,
                    &subject
                )
                .is_err()
        );
    }
    #[test]
    fn uncertain_otio_process_retains_original_and_blocks_another_dispatch_after_reopen() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("studio.sqlite3");
        let service = StudioService::open(&path).unwrap();
        let project = service.create_project("Retained OTIO attempt").unwrap();
        let peer = StudioService::open(&path).unwrap();
        let preview = OtioBridgeOperation::Preview;
        let export = OtioBridgeOperation::Export;
        let parent = root.path().canonicalize().unwrap();
        let workspace = |operation| {
            BridgeWorkspaceParent::admit(&parent)
                .unwrap()
                .create(operation)
                .unwrap()
        };

        let mut original = workspace(preview);
        let original_path = original.path().to_owned();
        fs::write(original_path.join("diagnostic.txt"), b"original diagnostic").unwrap();
        let outcome = run_tracked_bridge(&service, &project, preview, &mut original, || {
            let mut competing = workspace(preview);
            let error = run_tracked_bridge(&peer, &project, preview, &mut competing, || {
                panic!("a second process cannot dispatch while the original is running")
            })
            .unwrap_err();
            assert!(matches!(error, OtioBridgeError::Unsettled(_)));
            Err(BridgeExecutionFailure::Unsettled)
        });
        let Err(OtioBridgeError::Unsettled(original_result)) = outcome else {
            panic!("uncertain process did not return its original identity");
        };
        drop(original);
        drop(peer);
        drop(service);
        assert_eq!(
            fs::read(original_path.join("diagnostic.txt")).unwrap(),
            b"original diagnostic"
        );

        let reopened = StudioService::open(&path).unwrap();
        let pending =
            unsettled_bridge_operations(&reopened, project.id, project.generation).unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].attempt_id, original_result.attempt_id);
        assert_eq!(pending[0].workspace_name, original_result.workspace_name);
        let receipt = reopened
            .unsettled_otio_bridge(project.id, project.generation, preview)
            .unwrap()
            .unwrap();
        assert_eq!(receipt.stage, "outcome_unknown");
        let false_settlement = ProductionReceiptInput {
            project_id: receipt.project_id,
            generation: receipt.generation,
            revision: receipt.revision,
            request_id: receipt.request_id,
            request_sha256: receipt.request_sha256,
            command: receipt.command,
            stage: "bridge_stopped".into(),
            payload: receipt.payload,
        };
        assert!(matches!(
            reopened.append_production_receipt(false_settlement),
            Err(StorageError::RequestReuse)
        ));

        let mut competing = workspace(preview);
        let competing_path = competing.path().to_owned();
        let error = run_tracked_bridge(&reopened, &project, preview, &mut competing, || {
            panic!("an uncertain predecessor cannot replay after reopen")
        })
        .unwrap_err();
        assert!(matches!(error, OtioBridgeError::Unsettled(_)));
        drop(competing);
        assert!(!competing_path.exists());

        let mut independent = workspace(export);
        let independent_path = independent.path().to_owned();
        let early_failure =
            run_tracked_bridge(&reopened, &project, export, &mut independent, || {
                Err(BridgeExecutionFailure::Stopped("confirmed stop".into()))
            })
            .unwrap_err();
        assert_eq!(String::from(early_failure), "confirmed stop");
        drop(independent);
        assert!(!independent_path.exists());
        assert!(
            reopened
                .unsettled_otio_bridge(project.id, project.generation, export)
                .unwrap()
                .is_none()
        );
        let mut later = workspace(export);
        run_tracked_bridge(&reopened, &project, export, &mut later, || Ok(())).unwrap();
        assert!(
            reopened
                .unsettled_otio_bridge(project.id, project.generation, export)
                .unwrap()
                .is_none()
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn original_source_identity_rejects_same_size_name_replacement_and_symlink() {
        let root = tempfile::tempdir().unwrap();
        let original = root.path().join("owner-original.otio");
        let replacement = root.path().join("different-owner.otio");
        fs::write(&original, b"original cut").unwrap();
        fs::write(&replacement, b"foreign clip").unwrap();
        let original_meta = fs::symlink_metadata(&original).unwrap();
        assert_eq!(
            read_regular_bounded(&original, 64).unwrap(),
            b"original cut"
        );
        assert!(read_regular_bounded(&original, 5).is_err());

        fs::rename(&replacement, &original).unwrap();
        let changed_meta = fs::symlink_metadata(&original).unwrap();
        assert_eq!(original_meta.len(), changed_meta.len());
        assert!(!same_regular_source(&original_meta, &changed_meta));
        assert_eq!(
            read_regular_bounded(&original, 64).unwrap(),
            b"foreign clip"
        );

        let alias = root.path().join("symlinked-original.otio");
        std::os::unix::fs::symlink(&original, &alias).unwrap();
        assert!(read_regular_bounded(&alias, 64).is_err());
    }

    #[cfg(any(windows, target_os = "macos"))]
    #[test]
    fn portable_otio_uses_owner_paths_and_publishes_exclusively() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path().canonicalize().unwrap();
        #[cfg(windows)]
        let root = PathBuf::from(root.to_str().unwrap().strip_prefix(r"\\?\").unwrap());
        fs::create_dir(root.join("Source Folder")).unwrap();
        let media = root.join("original.mp4");
        fs::write(&media, b"original media fixture").unwrap();
        let stage = root.join("prepared bundle");
        fs::create_dir(&stage).unwrap();
        fs::write(stage.join("timeline.otio"), b"prepared timeline").unwrap();
        let occupied = root.join("occupied bundle");
        fs::create_dir(&occupied).unwrap();
        let destination = root.join("new bundle");

        assert!(canonical_owner_directory(root.join("Source Folder").to_str().unwrap()).is_ok());
        assert!(selected_owner_file(media.to_str().unwrap()).is_ok());
        assert!(new_owner_bundle_destination(destination.to_str().unwrap()).is_ok());
        assert!(publish_owner_bundle_no_clobber(&stage, &occupied).is_err());
        assert!(fs::read_dir(&occupied).unwrap().next().is_none());
        publish_owner_bundle_no_clobber(&stage, &destination).unwrap();
        assert!(!stage.exists());
        assert_eq!(
            fs::read(destination.join("timeline.otio")).unwrap(),
            b"prepared timeline"
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn portable_otio_publication_never_clobbers_a_racing_directory_or_dangling_symlink() {
        let root = tempfile::tempdir().unwrap();
        let staged = root.path().join("verified-private-output");
        fs::create_dir(&staged).unwrap();
        fs::write(staged.join("original-media.bin"), b"exact saved original").unwrap();

        let occupied = root.path().join("foreign-owner-directory");
        fs::create_dir(&occupied).unwrap();
        fs::write(occupied.join("foreign.bin"), b"do not overwrite").unwrap();
        assert!(!destination_unclaimed(&occupied).unwrap());
        assert!(publish_owner_bundle_no_clobber(&staged, &occupied).is_err());
        assert_eq!(
            fs::read(occupied.join("foreign.bin")).unwrap(),
            b"do not overwrite"
        );
        assert_eq!(
            fs::read(staged.join("original-media.bin")).unwrap(),
            b"exact saved original"
        );

        let dangling = root.path().join("dangling-bundle-link");
        std::os::unix::fs::symlink(root.path().join("missing-target"), &dangling).unwrap();
        assert!(!destination_unclaimed(&dangling).unwrap());
        assert!(new_owner_bundle_destination(dangling.to_str().unwrap()).is_err());
        assert!(publish_owner_bundle_no_clobber(&staged, &dangling).is_err());
        assert!(
            fs::symlink_metadata(&dangling)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        assert_eq!(
            fs::read(staged.join("original-media.bin")).unwrap(),
            b"exact saved original"
        );

        let accepted = root.path().join("first-owner-published");
        assert!(destination_unclaimed(&accepted).unwrap());
        publish_owner_bundle_no_clobber(&staged, &accepted).unwrap();
        assert!(!staged.exists());
        assert_eq!(
            fs::read(accepted.join("original-media.bin")).unwrap(),
            b"exact saved original"
        );
    }

    #[test]
    fn otio_original_source_choice_is_closed_and_has_no_implicit_fallback() {
        let base = serde_json::json!({
            "scene_id": Uuid::now_v7(), "asset_id": Uuid::now_v7(), "source_start_frame": 0
        });
        let mut legacy = base.clone();
        legacy["source_path"] = serde_json::json!("/owner/unchanged.mp4");
        assert!(
            !serde_json::from_value::<OtioOriginalMediaBinding>(legacy.clone())
                .unwrap()
                .uses_project_cas()
                .unwrap()
        );
        legacy["source_kind"] = serde_json::json!("owner_file");
        assert!(
            !serde_json::from_value::<OtioOriginalMediaBinding>(legacy)
                .unwrap()
                .uses_project_cas()
                .unwrap()
        );
        let mut cas = base.clone();
        cas["source_kind"] = serde_json::json!("project_cas");
        assert!(
            serde_json::from_value::<OtioOriginalMediaBinding>(cas.clone())
                .unwrap()
                .uses_project_cas()
                .unwrap()
        );
        cas["source_path"] = serde_json::json!("/owner/foreign.mp4");
        assert!(
            serde_json::from_value::<OtioOriginalMediaBinding>(cas.clone())
                .unwrap()
                .uses_project_cas()
                .is_err()
        );
        cas["source_path"] = serde_json::Value::Null;
        assert!(serde_json::from_value::<OtioOriginalMediaBinding>(cas).is_err());
        assert!(
            serde_json::from_value::<OtioOriginalMediaBinding>(base.clone())
                .unwrap()
                .uses_project_cas()
                .is_err()
        );
        for invalid_kind in [serde_json::Value::Null, serde_json::json!("unknown")] {
            let mut invalid = base.clone();
            invalid["source_kind"] = invalid_kind;
            assert!(serde_json::from_value::<OtioOriginalMediaBinding>(invalid).is_err());
        }
    }

    #[test]
    fn imported_otio_source_survives_original_path_move_and_store_reopen() {
        // Opaque test bytes exercise CAS custody only. The real ffprobe/NLE
        // admission remains in the existing media bridge integration test.
        let root = tempfile::tempdir().unwrap();
        let db = root.path().join("otio-source.sqlite3");
        let original = root.path().join("original.mp4");
        let bytes = b"synthetic custody fixture, not playable media";
        fs::write(&original, bytes).unwrap();
        let service = StudioService::open(&db).unwrap();
        let project = service
            .create_project("Synthetic OTIO source custody")
            .unwrap();
        let saved = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                "otio-source-original",
                &original,
                "original.mp4".into(),
                "video/mp4".into(),
            )
            .unwrap()
            .project;
        let asset = saved
            .assets
            .iter()
            .find(|asset| asset.content_sha256.as_deref() == Some(sha(bytes).as_str()))
            .unwrap()
            .clone();
        let digest = asset.content_sha256.as_deref().unwrap();
        fs::rename(&original, root.path().join("retained-original.mp4")).unwrap();
        assert!(!original.exists());
        drop(service);
        let reopened = StudioService::open(&db).unwrap();
        let current = reopened.project(saved.id).unwrap();
        assert_eq!(current, saved);
        assert_eq!(
            read_saved_otio_asset(&reopened, &current, asset.id, digest).unwrap(),
            bytes
        );
        assert!(read_saved_otio_asset(&reopened, &current, Uuid::now_v7(), digest).is_err());
        assert!(read_saved_otio_asset(&reopened, &current, asset.id, &"b".repeat(64)).is_err());
        let mut stale = current.clone();
        stale.revision += 1;
        assert!(read_saved_otio_asset(&reopened, &stale, asset.id, digest).is_err());
        assert_eq!(reopened.project(saved.id).unwrap(), saved);
    }

    #[test]
    fn request_rejects_foreign_fields_before_bridge_authority() {
        let input = serde_json::json!({
            "project_id": Uuid::now_v7(),
            "generation": Uuid::now_v7(),
            "revision": 7,
            "bundle_path": "/tmp/test",
            "edited_file": "owned.otio",
            "effect_grant": Uuid::now_v7(),
            "unverified_preview": true
        });
        assert!(serde_json::from_value::<OtioMediaPreviewRequest>(input.clone()).is_err());
        let mut allowed = input;
        allowed
            .as_object_mut()
            .unwrap()
            .remove("unverified_preview");
        assert!(
            serde_json::from_value::<OtioMediaPreviewRequest>(allowed.clone())
                .unwrap()
                .received_file
                .is_none()
        );
        allowed["received_file"] = serde_json::json!("received.otio");
        assert_eq!(
            serde_json::from_value::<OtioMediaPreviewRequest>(allowed)
                .unwrap()
                .received_file
                .as_deref(),
            Some("received.otio")
        );
        // A returned RAW shape can become only a typed, unaccepted proposal.
        // Full scene/edit/fidelity validation still belongs to bounded_native_changes.
        let id = Uuid::now_v7();
        let digest = "a".repeat(64);
        let mut native = serde_json::json!({
            "schema":"motionwright.kdenlive-bound-cut-preview/1",
            "profile":"kdenlive-bound-video-cut-v1",
            "status":"BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
            "capsule_manifest_sha256":digest, "original_otio_sha256":digest,
            "received_otio_sha256":digest, "edited_otio_sha256":digest,
            "source_project_sha256":digest, "project_id":id, "generation":Uuid::now_v7(),
            "expected_project_revision":7, "original_scene_order":[id],
            "proposed_scene_order":[id], "original_missing_scene_ids":[],
            "native_change_proposals":[], "media_refs_verified":1,
            "received_clip_count":1, "edited_clip_count":1,
            "source_identity_mapping":[{}], "loss_report":["Original property losses"],
            "property_fidelity":[], "nle_receiver_acceptance":"NOT_VERIFIED",
            "native_project_reopened":"NOT_VERIFIED", "native_final_frames_verified":false,
            "edit_imported":false, "source_media_relinked":false, "raw_files_rewritten":false,
            "owner_revision_commit_required":true, "human_review_required":true,
            "semantic_native_roundtrip":"NOT_SUPPORTED", "publication_approved":false
        });
        let project = |value: &serde_json::Value| {
            kdenlive_owner_preview(
                &serde_json::to_vec(value).unwrap(),
                &digest,
                &digest,
                &digest,
                &digest,
                &digest,
            )
        };
        let proposal = project(&native).unwrap();
        assert_eq!(proposal.schema, "motionwright.otio-edit-preview/1");
        assert!(!proposal.rendering_performed && !proposal.publication_approved);
        assert_eq!(proposal.nle_receiver_acceptance, "NOT_VERIFIED");
        assert!(proposal.loss_report.last().unwrap().contains(&digest));
        for field in [
            "received_otio_sha256",
            "edited_otio_sha256",
            "capsule_manifest_sha256",
            "source_project_sha256",
            "original_otio_sha256",
            "nle_receiver_acceptance",
            "native_project_reopened",
            "status",
        ] {
            let mut substituted = native.clone();
            substituted[field] = serde_json::json!("unbound");
            assert!(project(&substituted).is_err(), "{field}");
        }
        for field in [
            "edit_imported",
            "source_media_relinked",
            "raw_files_rewritten",
            "native_final_frames_verified",
            "publication_approved",
        ] {
            let mut forged = native.clone();
            forged[field] = serde_json::json!(true);
            assert!(project(&forged).is_err(), "{field}");
        }
        native["original_missing_scene_ids"] = serde_json::json!([id]);
        assert!(
            project(&native).is_err(),
            "missing source cannot become a complete receiver"
        );
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn unsafe_otio_folder_preserves_preview_grant_before_opening_documents() {
        use std::os::unix::fs::PermissionsExt;

        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().canonicalize().unwrap();
        let parent = root.join("shared");
        let bundle = parent.join("cut");
        fs::create_dir_all(&bundle).unwrap();
        fs::set_permissions(&parent, fs::Permissions::from_mode(0o777)).unwrap();

        let service = StudioService::open(root.join("studio.sqlite3")).unwrap();
        let project = service.create_project("Protected OTIO workspace").unwrap();
        let scope = EffectScope::project(project.id, project.generation, project.revision);
        let grants = EffectGrantRegistry::default();
        let bundle_path = bundle.to_str().unwrap();
        let subject = preview_subject(bundle_path, "edited.otio", None).unwrap();
        let grant = grants
            .issue(EffectKind::ImportLocal, scope, &subject)
            .unwrap();
        let error = preview(
            &service,
            &grants,
            OtioMediaPreviewRequest {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                bundle_path: bundle_path.into(),
                edited_file: "edited.otio".into(),
                received_file: None,
                effect_grant: grant.token,
            },
        )
        .unwrap_err();
        assert_eq!(String::from(error), WORKSPACE_REFUSED);
        assert!(
            grants
                .consume(grant.token, EffectKind::ImportLocal, scope, &subject)
                .is_ok()
        );
        assert_eq!(fs::read_dir(parent).unwrap().count(), 1);
    }

    #[cfg(not(target_os = "linux"))]
    #[test]
    fn unavailable_otio_runtime_preserves_both_grants_and_leaves_the_workspace_untouched() {
        struct RestoreRuntime;
        impl Drop for RestoreRuntime {
            fn drop(&mut self) {
                RUNTIME_UNAVAILABLE.set(false);
            }
        }
        let _restore_runtime = RestoreRuntime;
        RUNTIME_UNAVAILABLE.set(true);
        assert!(
            check_runtime_files()
                .unwrap_err()
                .starts_with("OTIO bridge preflight unavailable:")
        );
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("OTIO runtime preflight").unwrap();
        let with_scene = service
            .apply(
                initial.id,
                &initial.stamp(),
                "otio-preflight-fixture-scene",
                &motionwright_domain::Change::AddScene {
                    name: "Original cut".into(),
                    objective: "Verify OTIO runtime preflight".into(),
                    duration_seconds: 1,
                },
            )
            .unwrap()
            .project;
        let original = root.path().join("original.mp4");
        fs::write(&original, b"synthetic OTIO asset for preflight only").unwrap();
        let project = service
            .import_asset_file(
                with_scene.id,
                &with_scene.stamp(),
                "otio-preflight-fixture-asset",
                &original,
                "original.mp4".into(),
                "video/mp4".into(),
            )
            .unwrap()
            .project;
        let scope = EffectScope::project(project.id, project.generation, project.revision);
        let grants = EffectGrantRegistry::default();

        let abs = root.path().canonicalize().unwrap();
        let source = abs.join("owner-originals");
        fs::create_dir(&source).unwrap();
        fs::write(
            source.join("source-project.json"),
            serde_json::to_vec(&project).unwrap(),
        )
        .unwrap();
        fs::write(source.join("bridge-manifest.json"), b"{}").unwrap();
        let timeline = motionwright_domain::otio_interchange(&project).unwrap();
        fs::write(source.join("edited.otio"), timeline.body.as_bytes()).unwrap();
        let bundle = source.to_str().unwrap().to_owned();
        let subject = preview_subject(&bundle, "edited.otio", None).unwrap();
        let import = grants
            .issue(EffectKind::ImportLocal, scope, &subject)
            .unwrap();
        let preview_request = OtioMediaPreviewRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            bundle_path: bundle,
            edited_file: "edited.otio".into(),
            received_file: None,
            effect_grant: import.token,
        };
        assert_eq!(
            String::from(preview(&service, &grants, preview_request).unwrap_err()),
            format!(
                "OTIO bridge preflight unavailable: {}",
                fixed_runtime().unwrap_err()
            )
        );
        assert!(
            grants
                .consume(import.token, EffectKind::ImportLocal, scope, &subject)
                .is_ok()
        );

        let output = abs.join("owner-export");
        let destination = output.to_str().unwrap().to_owned();
        let export = grants
            .issue(EffectKind::DeliverLocal, scope, &destination)
            .unwrap();
        let export_request = OtioMediaBundleExportRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            destination: destination.clone(),
            reviewer_declaration: "Local reviewer".into(),
            media_owner: "Original author".into(),
            media_license: "Local interchange fixture".into(),
            authorized: true,
            redistributable: true,
            allow_synthetic: false,
            bindings: vec![OtioOriginalMediaBinding {
                scene_id: project.scenes[0].id,
                asset_id: project.assets[0].id,
                source_kind: Some(OtioOriginalSourceKind::ProjectCas),
                source_path: None,
                source_start_frame: 0,
            }],
            effect_grant: export.token,
        };
        assert_eq!(
            String::from(export_portable(&service, &grants, export_request).unwrap_err()),
            format!(
                "OTIO bridge preflight unavailable: {}",
                fixed_runtime().unwrap_err()
            )
        );
        assert!(
            grants
                .consume(export.token, EffectKind::DeliverLocal, scope, &destination)
                .is_ok()
        );
        assert!(!output.exists());
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[test]
    fn media_bundle_export_rejects_implicit_rights_and_noncanonical_origins() {
        assert!(!owner_declaration("", 250));
        assert!(!owner_declaration("a\nsecond line", 250));
        assert!(owner_declaration(
            "Explicit first-party source license",
            250
        ));
        assert!(selected_owner_file("relative-original.mp4").is_err());
        assert!(new_owner_bundle_destination("relative-new-bundle").is_err());
        let forged = serde_json::json!({
            "project_id": Uuid::now_v7(),
            "generation": Uuid::now_v7(),
            "revision": 7,
            "destination": "/tmp/source-export",
            "reviewer_declaration": "author",
            "media_owner": "author",
            "media_license": "private",
            "authorized": true,
            "redistributable": true,
            "allow_synthetic": false,
            "bindings": [],
            "effect_grant": Uuid::now_v7(),
            "skip_original_hashing": true,
        });
        assert!(serde_json::from_value::<OtioMediaBundleExportRequest>(forged).is_err());
    }
}

/// Explicit owner-selected source bindings. Asset IDs must already exist in
/// the saved Project and source bytes must match the original CAS digest.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
enum OtioOriginalSourceKind {
    ProjectCas,
    OwnerFile,
}

/// Missing legacy kind is accepted only with the original explicit file path.
/// Explicit ProjectCas has no client-supplied filesystem location.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OtioOriginalMediaBinding {
    pub scene_id: Uuid,
    pub asset_id: Uuid,
    #[serde(default, deserialize_with = "present_original_source_kind")]
    source_kind: Option<OtioOriginalSourceKind>,
    #[serde(default, deserialize_with = "present_original_source_path")]
    source_path: Option<String>,
    pub source_start_frame: u32,
}

fn present_original_source_kind<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<OtioOriginalSourceKind>, D::Error> {
    OtioOriginalSourceKind::deserialize(deserializer).map(Some)
}

fn present_original_source_path<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<String>, D::Error> {
    String::deserialize(deserializer).map(Some)
}

impl OtioOriginalMediaBinding {
    fn uses_project_cas(&self) -> Result<bool, String> {
        match (self.source_kind, self.source_path.as_deref()) {
            (Some(OtioOriginalSourceKind::ProjectCas), None) => Ok(true),
            (None | Some(OtioOriginalSourceKind::OwnerFile), Some(path))
                if !path.trim().is_empty() && path.trim() == path && path.len() <= 1800
                    && !path.chars().any(char::is_control) => Ok(false),
            _ => Err("Select either the saved Project MP4 or one explicit external original file, never both.".into()),
        }
    }
}

/// Project-owned source capture, using the existing verified CAS reader and
/// exact saved snapshot. No asset id alone grants permission to arbitrary blobs.
fn read_saved_otio_asset(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
    expected_sha256: &str,
) -> Result<Vec<u8>, String> {
    if !project.assets.iter().any(|asset| {
        asset.id == asset_id
            && asset.media_type == "video/mp4"
            && asset.content_sha256.as_deref() == Some(expected_sha256)
    }) {
        return Err("Saved OTIO media is not an exact Project-owned MP4 asset.".into());
    }
    let shared = service.store();
    let store = shared.lock();
    if store.load_project(project.id).map_err(|_| refused())? != *project {
        return Err("Project changed before saved OTIO media capture.".into());
    }
    let bytes = store.read_blob(expected_sha256, 256 * 1024 * 1024)
        .map_err(|_| "Saved Project MP4 is missing, altered or exceeds the source budget; no external fallback was used.".to_string())?;
    if bytes.is_empty() || sha(&bytes) != expected_sha256 {
        return Err(
            "Saved Project MP4 bytes differ from their exact immutable source digest.".into(),
        );
    }
    Ok(bytes)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OtioMediaBundleExportRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub destination: String,
    pub reviewer_declaration: String,
    pub media_owner: String,
    pub media_license: String,
    pub authorized: bool,
    pub redistributable: bool,
    pub allow_synthetic: bool,
    pub bindings: Vec<OtioOriginalMediaBinding>,
    pub effect_grant: Uuid,
}

#[derive(Debug, Serialize)]
pub struct OtioMediaBundleExportResponse {
    pub path: String,
    pub source_project_sha256: String,
    pub media_refs_verified: usize,
    pub missing_references: usize,
    pub source_media_relinked: bool,
    pub editor_launched: bool,
    pub project_modified: bool,
    pub nle_import_verified: bool,
    pub publication_authorized: bool,
}

fn owner_declaration(value: &str, maximum: usize) -> bool {
    !value.trim().is_empty()
        && value.trim() == value
        && value.len() <= maximum
        && !value.chars().any(char::is_control)
}

fn selected_owner_file(input: &str) -> Result<PathBuf, String> {
    if input.len() > 1800
        || input.trim() != input
        || input.is_empty()
        || input.chars().any(char::is_control)
    {
        return Err("Choose one absolute, unchanged owner media filename.".into());
    }
    let path = PathBuf::from(input);
    let canonical = canonical_plain_path(&path)?;
    if !canonical.is_file()
        || canonical
            .extension()
            .and_then(|ext| ext.to_str())
            .is_none_or(|ext| !ext.eq_ignore_ascii_case("mp4"))
    {
        return Err(
            "Owner media must be an original, regular, non-symlink absolute MP4 file.".into(),
        );
    }
    Ok(canonical)
}

/// The destination must not exist, including a dangling symlink.
fn destination_unclaimed(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(false),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(_) => Err("The new OTIO destination could not be checked safely.".into()),
    }
}

pub(super) fn new_owner_bundle_destination(value: &str) -> Result<PathBuf, String> {
    if value.is_empty()
        || value.trim() != value
        || value.len() > 1800
        || value.chars().any(char::is_control)
    {
        return Err("Choose one absolute new OTIO media bundle destination.".into());
    }
    let path = PathBuf::from(value);
    let parent = path.parent().ok_or_else(refused)?;
    let canonical_parent = canonical_plain_path(parent)?;
    #[cfg(windows)]
    if !windows_plain_components(&path) {
        return Err(refused());
    }
    if path.file_name().is_none() || !canonical_parent.is_dir() || !destination_unclaimed(&path)? {
        return Err(
            "The portable OTIO destination must be a new directory under a canonical owner folder."
                .into(),
        );
    }
    Ok(path)
}

/// Publish the complete verified directory through the storage layer's
/// exclusive rename, with the Windows canonical spelling at the OS boundary.
fn publish_owner_bundle_no_clobber(staged: &Path, destination: &Path) -> Result<(), String> {
    let staged = canonical_plain_path(staged)?;
    let parent = canonical_plain_path(destination.parent().ok_or_else(refused)?)?;
    let destination = parent.join(destination.file_name().ok_or_else(refused)?);
    motionwright_service::publish_bundle_directory(&staged, &destination).map_err(|_| {
        if matches!(destination_unclaimed(&destination), Ok(false)) {
            "The OTIO destination was claimed; no original files were replaced.".into()
        } else {
            "The original OTIO bundle could not be published atomically without overwrite.".into()
        }
    })
}

/// A first-party, bounded media bundle with real ExternalReference clips.
/// This is an export, not an NLE launch or a project/asset modification.
pub fn export_portable(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: OtioMediaBundleExportRequest,
) -> Result<OtioMediaBundleExportResponse, OtioBridgeError> {
    if !request.authorized
        || !request.redistributable
        || !owner_declaration(&request.reviewer_declaration, 200)
        || !owner_declaration(&request.media_owner, 250)
        || !owner_declaration(&request.media_license, 250)
        || request.bindings.is_empty()
        || request.bindings.len() > 32
    {
        return Err("Portable OTIO media export requires explicit original ownership, media redistribution permission and one bounded scene binding.".into());
    }
    let runtime = admit_runtime_before_dispatch()?;
    let destination = new_owner_bundle_destination(&request.destination)?;
    let project = service.project(request.project_id).map_err(|_| refused())?;
    if project.generation != request.generation
        || project.revision != request.revision
        || project.scenes.is_empty()
        || project.scenes.len() > 32
    {
        return Err("Portable OTIO source revision or scene count has changed.".into());
    }
    let mut scene_ids = std::collections::BTreeSet::new();
    for binding in &request.bindings {
        binding.uses_project_cas()?;
        if !scene_ids.insert(binding.scene_id)
            || !project
                .scenes
                .iter()
                .any(|scene| scene.id == binding.scene_id)
            || !project.assets.iter().any(|asset| {
                asset.id == binding.asset_id
                    && asset.media_type == "video/mp4"
                    && asset.content_sha256.as_deref().is_some_and(|digest| {
                        digest.len() == 64
                            && digest
                                .bytes()
                                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                    })
            })
        {
            return Err(
                "Every selected source scene and MP4 asset must belong to this exact Project."
                    .into(),
            );
        }
    }
    let project_bytes = serde_json::to_vec(&project).map_err(|_| refused())?;
    let interchange = motionwright_domain::otio_interchange(&project).map_err(|_| {
        "The original Project has no supported conservative 24fps OTIO cut.".to_string()
    })?;
    if project_bytes.is_empty()
        || project_bytes.len() as u64 > MAX_PROJECT
        || interchange.body.len() as u64 > MAX_PROJECT
    {
        return Err(
            "The original OTIO source exceeds the bounded 2 MiB interchange profile.".into(),
        );
    }

    let operation = OtioBridgeOperation::Export;
    require_bridge_idle(service, &project, operation)?;
    let parent = BridgeWorkspaceParent::admit(destination.parent().ok_or_else(refused)?)?;
    // Consume the exact one-use grant before creating a workspace or reading
    // original media. Runtime qualification is repeated at process launch.
    grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        &request.destination,
    )?;
    let mut temp = parent.create(operation)?;
    let (source_dir, bridge) = stage_bridge_sources(temp.path())?;
    let _original_sources = temp.hold_sources(&source_dir)?;
    let raw_root = temp.path().join("owner-originals");
    fs::create_dir(&raw_root).map_err(|_| refused())?;
    write_exact(&raw_root, "project.json", &project_bytes)?;
    write_exact(&raw_root, "baseline.otio", interchange.body.as_bytes())?;

    let mut staged_by_sha = std::collections::BTreeMap::<String, Vec<u8>>::new();
    let mut original_by_path = std::collections::BTreeMap::<PathBuf, String>::new();
    let mut original_by_asset = std::collections::BTreeMap::<Uuid, String>::new();
    let mut bindings = Vec::new();
    let mut unique_bytes = 0u64;
    for selected in &request.bindings {
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == selected.asset_id)
            .ok_or_else(refused)?;
        let digest = asset.content_sha256.as_deref().ok_or_else(refused)?;
        let (source, bytes) = if selected.uses_project_cas()? {
            let bytes = read_saved_otio_asset(service, &project, selected.asset_id, digest)?;
            original_by_asset.insert(selected.asset_id, digest.to_owned());
            (None, bytes)
        } else {
            let source = selected_owner_file(selected.source_path.as_deref().ok_or_else(refused)?)?;
            let bytes = read_regular_bounded(&source, 256 * 1024 * 1024)?;
            (Some(source), bytes)
        };
        if sha(&bytes) != digest {
            return Err("Owner MP4 bytes do not match the saved Project asset SHA-256.".into());
        }
        if let Some(earlier) = staged_by_sha.get(digest) {
            if earlier != &bytes {
                return Err(
                    "Two owner media inputs claimed the same original digest but differ in bytes."
                        .into(),
                );
            }
        } else {
            unique_bytes = unique_bytes
                .checked_add(bytes.len() as u64)
                .ok_or_else(refused)?;
            if unique_bytes > 512 * 1024 * 1024 {
                return Err(
                    "The original OTIO media source exceeds the 512 MiB aggregate bound.".into(),
                );
            }
            write_exact(&raw_root, &format!("owned-{digest}.mp4"), &bytes)?;
            staged_by_sha.insert(digest.to_owned(), bytes);
        }
        if let Some(source) = source
            && original_by_path
                .insert(source, digest.to_owned())
                .is_some_and(|previous| previous != digest)
        {
            return Err(
                "A previously selected original source path changed its asset identity.".into(),
            );
        }
        bindings.push(serde_json::json!({
            "scene_id": selected.scene_id,
            "asset_id": selected.asset_id,
            "file": format!("owned-{digest}.mp4"),
            "sha256": digest,
            "rights": {
                "owner": request.media_owner.as_str(),
                "license": request.media_license.as_str(),
                "authorized": true,
                "redistributable": true,
            },
            "source_start_frame": selected.source_start_frame,
        }));
    }
    let export_request = serde_json::json!({
        "schema": "motionwright.otio-portable-media-bridge/1",
        "project": "project.json",
        "otio": "baseline.otio",
        "bindings": bindings,
        "owner_review": {
            "reviewer": request.reviewer_declaration,
            "synthetic_source_allowed": request.allow_synthetic,
        }
    });
    let request_bytes = serde_json::to_vec(&export_request).map_err(|_| refused())?;
    if request_bytes.len() as u64 > MAX_PROJECT {
        return Err("The original OTIO media export request is too large.".into());
    }
    write_exact(&raw_root, "media-request.json", &request_bytes)?;
    if !destination_unclaimed(&destination)? {
        return Err("Portable OTIO destination was claimed by another writer.".into());
    }
    // The Python bridge writes only inside the private staging tree.
    // Publish its validated directory to the owner path once, atomically.
    let pending = temp.path().join("verified-original-otio-bundle");
    let arguments = vec![
        OsString::from("export"),
        OsString::from("--root"),
        raw_root.as_os_str().to_owned(),
        OsString::from("--request"),
        OsString::from("media-request.json"),
        OsString::from("--output"),
        pending.as_os_str().to_owned(),
    ];
    run_tracked_bridge(service, &project, operation, &mut temp, || {
        run_bridge(&runtime, &source_dir, &bridge, &arguments)
    })?;

    // Check the actual final bundle and original inputs, not just the
    // process exit code. The bridge alone owns real ffprobe frame checks.
    let manifest_bytes = read_regular_bounded(&pending.join("bridge-manifest.json"), MAX_PROJECT)?;
    let manifest: serde_json::Value =
        serde_json::from_slice(&manifest_bytes).map_err(|_| refused())?;
    if manifest.get("schema").and_then(|v| v.as_str())
        != Some("motionwright.otio-portable-media-bridge/1")
        || manifest.get("project_sha256").and_then(|v| v.as_str())
            != Some(sha(&project_bytes).as_str())
        || manifest.get("project_id").and_then(|v| v.as_str())
            != Some(request.project_id.to_string().as_str())
        || manifest
            .get("nle_import_verified")
            .and_then(|v| v.as_bool())
            != Some(false)
        || manifest
            .get("publishing_authorized")
            .and_then(|v| v.as_bool())
            != Some(false)
        || manifest
            .get("original_project_unchanged")
            .and_then(|v| v.as_bool())
            != Some(true)
        || manifest
            .get("bound_media")
            .and_then(|v| v.as_array())
            .is_none_or(|items| items.len() != request.bindings.len())
        || read_regular_bounded(&pending.join("source-project.json"), MAX_PROJECT)? != project_bytes
    {
        return Err("The exported first-party OTIO bundle or preserved source has changed.".into());
    }
    for (digest, data) in &staged_by_sha {
        let original = read_regular_bounded(
            &pending.join("media").join(format!("{digest}.mp4")),
            256 * 1024 * 1024,
        )?;
        if &original != data {
            return Err(
                "An exported OTIO media file differs from the exact saved original.".into(),
            );
        }
    }
    for (path, digest) in &original_by_path {
        if sha(&read_regular_bounded(path, 256 * 1024 * 1024)?) != digest.as_str() {
            return Err("Owner original MP4 changed during portable export.".into());
        }
    }
    // Re-read each selected CAS identity after actual bridge/ffprobe output
    // validation. Missing or changed bytes never switch to an owner path.
    for (asset_id, digest) in &original_by_asset {
        let original = read_saved_otio_asset(service, &project, *asset_id, digest)?;
        if staged_by_sha.get(digest) != Some(&original) {
            return Err("Saved Project MP4 changed during portable export.".into());
        }
    }
    if service.project(request.project_id).map_err(|_| refused())? != project {
        return Err("Project changed during original OTIO media export; this artifact cannot be bound to the latest revision.".into());
    }
    // No owner-visible directory exists until *all* source and readback
    // checks complete. Never replace a racing directory or symlink.
    publish_owner_bundle_no_clobber(&pending, &destination)?;
    Ok(OtioMediaBundleExportResponse {
        path: request.destination,
        source_project_sha256: sha(&project_bytes),
        media_refs_verified: request.bindings.len(),
        missing_references: project.scenes.len() - request.bindings.len(),
        source_media_relinked: false,
        editor_launched: false,
        project_modified: false,
        nle_import_verified: false,
        publication_authorized: false,
    })
}
