//! Source-bound, read-only OTIO cut inspection for the installed desktop.
//! This embeds the existing first-party bridge source. A fixed root-owned
//! Linux Python+ffprobe runtime must exist or the operation fails closed.
//! The bridge does not import project media or approve a cut; only the
//! ordinary human-reviewed StudioService Change can modify the project.
use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::{OtioEditPreview, Project};
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
use uuid::Uuid;

const BRIDGE: &[u8] = include_bytes!("../../../../tooling/otio-media/bridge.py");
const AUDIO: &[u8] =
    include_bytes!("../../../../tooling/otio-media/kdenlive_firstparty_audio_track.py");
const INTAKE: &[u8] = include_bytes!("../../../../tooling/mlt_official_runtime_intake.py");
// -I/-S remove project and site imports. Admit only the two first-party
// module folders staged and SHA-checked above, never the owner's bundle.
const RUNNER: &[u8] = b"import os,runpy,sys\nsource=sys.argv.pop(1)\nsource_dir=os.path.dirname(source)\nsys.path.insert(0,os.path.dirname(source_dir))\nsys.path.insert(0,source_dir)\nrunpy.run_path(source,run_name='__main__')\n";
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
    pub effect_grant: Uuid,
}

#[derive(Debug, Serialize)]
pub struct OtioMediaPreviewResponse {
    pub preview: OtioEditPreview,
    pub source_project_sha256: String,
    pub media_refs_verified: usize,
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

fn canonical_owner_directory(path: &str) -> Result<PathBuf, String> {
    if path.trim() != path
        || path.is_empty()
        || path.len() > 1800
        || path.chars().any(char::is_control)
    {
        return Err("Choose one absolute, bounded original OTIO bundle folder.".into());
    }
    let input = PathBuf::from(path);
    if !input.is_absolute()
        || !input.is_dir()
        || input
            .symlink_metadata()
            .map_err(|_| refused())?
            .file_type()
            .is_symlink()
    {
        return Err("Choose an existing, non-symlink original OTIO bundle.".into());
    }
    if fs::canonicalize(&input).map_err(|_| refused())? != input
        || input.ancestors().any(|ancestor| {
            ancestor
                .symlink_metadata()
                .is_ok_and(|meta| meta.file_type().is_symlink())
        })
    {
        return Err("An original OTIO bundle cannot contain symlinked parent paths.".into());
    }
    Ok(input)
}

fn same_regular_source(before: &fs::Metadata, after: &fs::Metadata) -> bool {
    if !before.is_file()
        || !after.is_file()
        || before.len() != after.len()
        || before.modified().ok() != after.modified().ok()
    {
        return false;
    }
    #[cfg(target_os = "linux")]
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
    true
}

fn read_regular_bounded(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let before_name = path.symlink_metadata().map_err(|_| refused())?;
    if !before_name.file_type().is_file() || before_name.len() == 0 || before_name.len() > limit {
        return Err(refused());
    }
    // Read the same held regular handle, bounded even if a source file grows.
    // Re-observe the descriptor and original pathname after the read so a
    // same-sized replacement is not mistaken for the original file.
    let mut file = File::open(path).map_err(|_| refused())?;
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

#[cfg(target_os = "linux")]
fn fixed_runtime() -> Result<(PathBuf, PathBuf), String> {
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
    Ok((
        root_owned("/usr/bin/python3")?,
        root_owned("/usr/bin/ffprobe")?,
    ))
}

#[cfg(not(target_os = "linux"))]
fn fixed_runtime() -> Result<(PathBuf, PathBuf), String> {
    Err("This installed platform has no admitted OTIO Python+ffprobe bridge runtime. External proposals remain reviewable, but automated media inspection is unavailable.".into())
}

fn stage_bridge_sources(temp: &Path) -> Result<(PathBuf, PathBuf), String> {
    let tooling = temp.join("tooling");
    let source_dir = tooling.join("otio-media");
    fs::create_dir_all(&source_dir).map_err(|_| refused())?;
    let bridge = write_exact(&source_dir, "bridge.py", BRIDGE)?;
    write_exact(&source_dir, "kdenlive_firstparty_audio_track.py", AUDIO)?;
    write_exact(&tooling, "mlt_official_runtime_intake.py", INTAKE)?;
    write_exact(&source_dir, "run-original-bridge.py", RUNNER)?;
    Ok((source_dir, bridge))
}

/// Kill the entire dedicated Python+ffprobe group, not just its parent.
/// The installed runtime on non-Linux platforms is refused before spawning.
fn stop_bridge_group(child: &mut std::process::Child) -> bool {
    #[cfg(target_os = "linux")]
    {
        let group = i32::try_from(child.id()).ok().filter(|id| *id > 1);
        let signaled = group.is_some_and(|id| {
            // SAFETY: Linux kill receives a positive, bounded observed child PID
            // negated to signal only the fresh process group created below.
            // No user-supplied PID or group is ever used.
            unsafe { libc::kill(-id, libc::SIGKILL) == 0 }
        });
        let reaped = child.wait().is_ok();
        signaled && reaped
    }
    #[cfg(not(target_os = "linux"))]
    {
        let signaled = child.kill().is_ok();
        let reaped = child.wait().is_ok();
        signaled && reaped
    }
}

fn run_bridge(scripts: &Path, bridge: &Path, arguments: &[OsString]) -> Result<(), String> {
    let (python, _ffprobe) = fixed_runtime()?;
    let stderr_path = scripts.join("original-bridge-stderr.txt");
    let stderr = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&stderr_path)
        .map_err(|_| refused())?;
    let home = scripts.parent().ok_or_else(refused)?;
    let mut launch = Command::new(python);
    launch
        .args(["-I", "-S", "-B"])
        .arg(scripts.join("run-original-bridge.py"))
        .arg(bridge)
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", home)
        .env("TMPDIR", home)
        .env("LANG", "C.UTF-8")
        .current_dir(home)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::from(stderr));
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::process::CommandExt;
        // Python and every ffprobe descendant share this new group. The
        // timeout must retire all of them, not merely the Python parent.
        launch.process_group(0);
    }
    let mut child = launch
        .spawn()
        .map_err(|_| "The protected OTIO bridge process could not start.".to_string())?;
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(_)) => {
                // A failed Python bridge cannot be allowed to leave an
                // ffprobe descendant running. The denied action is never
                // replayed; consume this invocation's process group once.
                let _ = stop_bridge_group(&mut child);
                return Err(refused());
            }
            Ok(None) if started.elapsed() < DEADLINE => thread::sleep(Duration::from_millis(40)),
            Ok(None) => {
                let closed = stop_bridge_group(&mut child);
                return Err(if closed {
                    "The protected OTIO media inspection timed out; its entire dedicated process group was terminated. No project change was made.".into()
                } else {
                    "The OTIO process-group cleanup could not be proven. Do not retry the uncertain external media operation.".into()
                });
            }
            Err(_) => {
                let _ = stop_bridge_group(&mut child);
                return Err("The original OTIO bridge or its process group could not be observed through a clean terminal exit. No automatic retry is permitted.".into());
            }
        }
    }
}

/// Every input is source-scoped and the one-use local-import grant is consumed
/// before Python/ffprobe starts. A complete original media bundle is required:
/// an arbitrary .otio file, pasted claim or unverified JSON is insufficient.
pub fn preview(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: OtioMediaPreviewRequest,
) -> Result<OtioMediaPreviewResponse, String> {
    if !valid_edited_name(&request.edited_file) {
        return Err(
            "Choose a single edited .otio filename inside the original source bundle.".into(),
        );
    }
    let bundle = canonical_owner_directory(&request.bundle_path)?;
    let project = service.project(request.project_id).map_err(|_| refused())?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err(
            "The OTIO source revision changed. Refresh and export a new source bundle.".into(),
        );
    }
    let original = read_regular_bounded(&bundle.join("source-project.json"), MAX_PROJECT)?;
    let source_project: Project = serde_json::from_slice(&original).map_err(|_| {
        "OTIO source-project.json must contain one complete original Motionwright Project."
            .to_string()
    })?;
    if source_project != project {
        return Err("The original OTIO media bundle does not belong to the currently saved project revision.".into());
    }
    let edited = read_regular_bounded(&bundle.join(&request.edited_file), MAX_PROJECT)?;
    if edited.len() < 16 {
        return Err(refused());
    }
    let subject = serde_json::to_string(&(
        "otio-bridge-preview/1",
        request.bundle_path.as_str(),
        request.edited_file.as_str(),
    ))
    .map_err(|_| refused())?;
    grants.consume(
        request.effect_grant,
        EffectKind::ImportLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        &subject,
    )?;
    // Fixed system runtime must exist before creating any disposable stage.
    fixed_runtime()?;
    let parent = bundle.parent().ok_or_else(refused)?;
    let temp = tempfile::Builder::new()
        .prefix(".motionwright-otio-preview-")
        .tempdir_in(parent)
        .map_err(|_| {
            "The original OTIO bundle parent must permit one isolated temporary review workspace."
                .to_string()
        })?;
    let (source_dir, bridge) = stage_bridge_sources(temp.path())?;
    let snapshot = write_exact(temp.path(), "current-project.json", &original)?;
    let output = temp.path().join("review-proposal.json");
    let arguments = vec![
        OsString::from("preview-import"),
        OsString::from("--bundle"),
        bundle.as_os_str().to_owned(),
        OsString::from("--edited"),
        OsString::from(request.edited_file.as_str()),
        OsString::from("--current-project"),
        snapshot.as_os_str().to_owned(),
        OsString::from("--output"),
        output.as_os_str().to_owned(),
    ];
    run_bridge(&source_dir, &bridge, &arguments)?;
    if read_regular_bounded(&bundle.join("source-project.json"), MAX_PROJECT)? != original
        || read_regular_bounded(&bundle.join(&request.edited_file), MAX_PROJECT)? != edited
    {
        return Err("The original OTIO source or edited cut changed during one media verification. Nothing was saved.".into());
    }
    let raw = read_regular_bounded(&output, MAX_PREVIEW)?;
    let preview: OtioEditPreview = serde_json::from_slice(&raw).map_err(|_| refused())?;
    if preview.source_project_sha256 != sha(&original)
        || preview.project_id != project.id
        || preview.generation != project.generation
        || preview.expected_project_revision != project.revision
        || preview.rendering_performed
        || preview.source_media_relinked
        || preview.edit_imported
        || preview.publication_approved
    {
        return Err(refused());
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
        preview,
        runtime: "system-root-owned-linux-python3-ffprobe; original first-party bridge source embedded",
        source_media_relinked: false,
        editor_launched: false,
        project_modified: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
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
        assert!(serde_json::from_value::<OtioMediaPreviewRequest>(input).is_err());
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
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OtioOriginalMediaBinding {
    pub scene_id: Uuid,
    pub asset_id: Uuid,
    pub source_path: String,
    pub source_start_frame: u32,
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
    if !path.is_absolute()
        || !path.is_file()
        || path
            .extension()
            .and_then(|ext| ext.to_str())
            .is_none_or(|ext| !ext.eq_ignore_ascii_case("mp4"))
        || path
            .symlink_metadata()
            .is_err_or(|meta| !meta.file_type().is_file())
        || fs::canonicalize(&path).map_err(|_| refused())? != path
        || path.ancestors().any(|parent| {
            parent
                .symlink_metadata()
                .is_ok_and(|meta| meta.file_type().is_symlink())
        })
    {
        return Err(
            "Owner media must be an original, regular, non-symlink absolute MP4 file.".into(),
        );
    }
    Ok(path)
}

/// The destination must not exist, including a dangling symlink.
fn destination_unclaimed(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(false),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(_) => Err("The new OTIO destination could not be checked safely.".into()),
    }
}

fn new_owner_bundle_destination(value: &str) -> Result<PathBuf, String> {
    if value.is_empty()
        || value.trim() != value
        || value.len() > 1800
        || value.chars().any(char::is_control)
    {
        return Err("Choose one absolute new OTIO media bundle destination.".into());
    }
    let path = PathBuf::from(value);
    let parent = path.parent().ok_or_else(refused)?;
    if !path.is_absolute()
        || !destination_unclaimed(&path)?
        || !parent.is_dir()
        || fs::canonicalize(parent).map_err(|_| refused())? != parent
        || parent.ancestors().any(|part| {
            part.symlink_metadata()
                .is_ok_and(|meta| meta.file_type().is_symlink())
        })
    {
        return Err(
            "The portable OTIO destination must be a new directory under a canonical owner folder."
                .into(),
        );
    }
    Ok(path)
}

/// Publish only after source, rights, media and live Project revalidation.
/// RENAME_NOREPLACE is mandatory: checking `exists()` then renaming can
/// overwrite a foreign empty directory or a symlink during an owner race.
#[cfg(target_os = "linux")]
fn publish_owner_bundle_no_clobber(staged: &Path, destination: &Path) -> Result<(), String> {
    use std::os::unix::ffi::OsStrExt;
    let from = std::ffi::CString::new(staged.as_os_str().as_bytes()).map_err(|_| refused())?;
    let to = std::ffi::CString::new(destination.as_os_str().as_bytes()).map_err(|_| refused())?;
    // SAFETY: NUL-free local paths and the fixed RENAME_NOREPLACE Linux flag.
    // The kernel performs one atomic no-overwrite rename, including symlinks.
    let result = unsafe {
        libc::renameat2(
            libc::AT_FDCWD,
            from.as_ptr(),
            libc::AT_FDCWD,
            to.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    if result == 0 {
        Ok(())
    } else if std::io::Error::last_os_error().kind() == std::io::ErrorKind::AlreadyExists {
        Err("The OTIO destination was claimed; no original files were replaced.".into())
    } else {
        Err("The original OTIO bundle could not be published atomically without overwrite.".into())
    }
}

#[cfg(not(target_os = "linux"))]
fn publish_owner_bundle_no_clobber(_staged: &Path, _destination: &Path) -> Result<(), String> {
    Err("This installed platform has no approved no-clobber OTIO media exporter.".into())
}

/// A first-party, bounded media bundle with real ExternalReference clips.
/// This is an export, not an NLE launch or a project/asset modification.
pub fn export_portable(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: OtioMediaBundleExportRequest,
) -> Result<OtioMediaBundleExportResponse, String> {
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
    // The owner explicitly approves a new local media export. Consume its
    // original one-use DeliverLocal grant before reading/copying the media.
    grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        &request.destination,
    )?;
    fixed_runtime()?;
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

    let parent = destination.parent().ok_or_else(refused)?;
    let temp = tempfile::Builder::new()
        .prefix(".motionwright-otio-export-")
        .tempdir_in(parent)
        .map_err(|_| "Unable to stage an isolated original OTIO export.".to_string())?;
    let (source_dir, bridge) = stage_bridge_sources(temp.path())?;
    let raw_root = temp.path().join("owner-originals");
    fs::create_dir(&raw_root).map_err(|_| refused())?;
    write_exact(&raw_root, "project.json", &project_bytes)?;
    write_exact(&raw_root, "baseline.otio", interchange.body.as_bytes())?;

    let mut staged_by_sha = std::collections::BTreeMap::<String, Vec<u8>>::new();
    let mut original_by_path = std::collections::BTreeMap::<PathBuf, String>::new();
    let mut bindings = Vec::new();
    let mut unique_bytes = 0u64;
    for selected in &request.bindings {
        let source = selected_owner_file(&selected.source_path)?;
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == selected.asset_id)
            .ok_or_else(refused)?;
        let digest = asset.content_sha256.as_deref().ok_or_else(refused)?;
        let bytes = read_regular_bounded(&source, 256 * 1024 * 1024)?;
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
        if original_by_path
            .insert(source.clone(), digest.to_owned())
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
    // The Python bridge writes only inside the private, 0700 staging tree.
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
    run_bridge(&source_dir, &bridge, &arguments)?;

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
    if service.project(request.project_id).map_err(|_| refused())? != project {
        return Err("Project changed during original OTIO media export; this artifact cannot be bound to the latest revision.".into());
    }
    // No owner-visible directory exists until *all* source and readback
    // checks complete. Never replace a racing directory or symlink.
    publish_owner_bundle_no_clobber(&pending, &destination)?;
    Ok(OtioMediaBundleExportResponse {
        path: destination.display().to_string(),
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
