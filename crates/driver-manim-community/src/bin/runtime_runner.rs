//! Bounded Host-mediated launcher for the Motionwright Manim Community profile.
//!
//! Driver Host supplies exact sealed Python/FFmpeg dependencies and owner-granted
//! runtime/work/output roots. This runner owns the only Python module invocation;
//! project/model data can never select an executable, module, source program, shell
//! fragment, environment variable, or arbitrary Manim CLI option.

use motionwright_manim_profile::semantic_graph_profile_probe::{
    fixed_graph_profile_probe_program_v1, parse_fixed_graph_profile_observation_v1,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    ffi::OsString,
    fs,
    path::{Component, Path, PathBuf},
    process::{Command, ExitStatus},
};

const MANIM_VERSION: &str = "0.21.0";
const SCENE_CLASS: &str = "MotionwrightScene";
const MAX_VIDEO_BYTES: u64 = 512 * 1024 * 1024;
const MAX_CAPTURE_BYTES: usize = 64 * 1024;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeReceipt {
    schema: u32,
    manim_version: String,
    pyav_version: String,
    source_color_policy: String,
    color_recipe: String,
}

#[derive(Debug)]
struct RenderArgs {
    runtime_root: PathBuf,
    python: PathBuf,
    ffmpeg: PathBuf,
    fontconfig_root: PathBuf,
    work_root: PathBuf,
    output_root: PathBuf,
    source: String,
    output_name: String,
    width: u32,
    height: u32,
    fps: u32,
}

fn fail(message: impl AsRef<str>) -> ! {
    eprintln!("{}", message.as_ref());
    std::process::exit(2);
}

fn direct_component(value: &str, label: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 128
        || value == "."
        || value == ".."
        || value.contains('/')
        || value.contains('\\')
        || value.contains('\0')
        || value.chars().any(char::is_control)
    {
        return Err(format!("{label} must be one bounded path component"));
    }
    Ok(())
}

fn real_directory(path: &Path, label: &str) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err(format!("{label} must be absolute"));
    }
    let metadata = fs::symlink_metadata(path).map_err(|_| format!("{label} is unavailable"))?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(format!("{label} must be a real directory"));
    }
    fs::canonicalize(path).map_err(|_| format!("{label} could not be canonicalized"))
}

fn sealed_executable(path: &Path, label: &str) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err(format!(
            "{label} must be an absolute Host-materialized path"
        ));
    }
    let metadata = fs::symlink_metadata(path).map_err(|_| format!("{label} is unavailable"))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!("{label} must be a regular non-symlink file"));
    }
    Ok(path.to_path_buf())
}

fn source_file(root: &Path, name: &str) -> Result<PathBuf, String> {
    direct_component(name, "Manim source filename")?;
    if !name.starts_with("mw_") || !name.ends_with(".py") {
        return Err("Manim source filename is outside the generated profile".into());
    }
    let path = root.join(name);
    let metadata = fs::symlink_metadata(&path)
        .map_err(|_| "generated Manim source is unavailable".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("generated Manim source must be a regular non-symlink file".into());
    }
    let canonical = fs::canonicalize(path)
        .map_err(|_| "generated Manim source could not be canonicalized".to_string())?;
    if !canonical.starts_with(root) {
        return Err("generated Manim source escaped the work root".into());
    }
    Ok(canonical)
}

fn parse_u32(value: &str, label: &str, min: u32, max: u32) -> Result<u32, String> {
    let parsed = value
        .parse::<u32>()
        .map_err(|_| format!("{label} is not an integer"))?;
    if !(min..=max).contains(&parsed) {
        return Err(format!("{label} is outside certified bounds"));
    }
    Ok(parsed)
}

fn parse_render_args(raw: Vec<String>) -> Result<RenderArgs, String> {
    if raw.first().map(String::as_str) != Some("render") {
        return Err("runner operation must be render".into());
    }
    let rest = &raw[1..];
    if rest.len() != 24 {
        return Err("runner requires the exact bounded render argument set".into());
    }
    let mut values = BTreeMap::new();
    for index in (0..rest.len()).step_by(2) {
        let key = rest[index].as_str();
        if !matches!(
            key,
            "--runtime-root"
                | "--python-sealed"
                | "--ffmpeg-sealed"
                | "--fontconfig-root"
                | "--work-root"
                | "--output-root"
                | "--source"
                | "--scene"
                | "--output-name"
                | "--width"
                | "--height"
                | "--fps"
        ) {
            return Err("runner received an unsupported option".into());
        }
        if values
            .insert(key.to_owned(), rest[index + 1].clone())
            .is_some()
        {
            return Err("runner option was duplicated".into());
        }
    }
    let required = |name: &str| {
        values
            .get(name)
            .cloned()
            .ok_or_else(|| format!("runner option {name} is missing"))
    };
    let width = parse_u32(&required("--width")?, "width", 320, 7680)?;
    let height = parse_u32(&required("--height")?, "height", 240, 4320)?;
    let fps = parse_u32(&required("--fps")?, "fps", 1, 120)?;
    if u64::from(width) * u64::from(height) > 33_177_600 {
        return Err("render pixel budget exceeds certified bounds".into());
    }
    let scene = required("--scene")?;
    if scene != SCENE_CLASS {
        return Err("runner scene class is not the Motionwright fixed profile".into());
    }
    let output_name = required("--output-name")?;
    direct_component(&output_name, "Manim output name")?;
    if !output_name.starts_with("mw_") {
        return Err("Manim output name is outside the generated profile".into());
    }
    let source = required("--source")?;
    direct_component(&source, "Manim source filename")?;

    Ok(RenderArgs {
        runtime_root: PathBuf::from(required("--runtime-root")?),
        python: PathBuf::from(required("--python-sealed")?),
        ffmpeg: PathBuf::from(required("--ffmpeg-sealed")?),
        fontconfig_root: PathBuf::from(required("--fontconfig-root")?),
        work_root: PathBuf::from(required("--work-root")?),
        output_root: PathBuf::from(required("--output-root")?),
        source,
        output_name,
        width,
        height,
        fps,
    })
}

fn runtime_site_packages(runtime_root: &Path) -> Result<PathBuf, String> {
    let receipt_path = runtime_root.join("runtime.json");
    let receipt_meta = fs::symlink_metadata(&receipt_path)
        .map_err(|_| "Manim runtime receipt is unavailable".to_string())?;
    if receipt_meta.file_type().is_symlink() || !receipt_meta.is_file() {
        return Err("Manim runtime receipt must be a regular file".into());
    }
    let bytes = fs::read(&receipt_path)
        .map_err(|_| "Manim runtime receipt could not be read".to_string())?;
    if bytes.len() > 4096 {
        return Err("Manim runtime receipt exceeds its bound".into());
    }
    let receipt: RuntimeReceipt = serde_json::from_slice(&bytes)
        .map_err(|_| "Manim runtime receipt is malformed".to_string())?;
    if receipt.schema != 2
        || receipt.manim_version != MANIM_VERSION
        || receipt.pyav_version != "15.0.0"
        || receipt.source_color_policy != "srgb-rgba8-opaque-v1"
        || receipt.color_recipe != "srgb-to-rec709-full12-v2"
    {
        return Err("Manim runtime receipt version does not match the certified profile".into());
    }
    let packages = runtime_root.join("site-packages");
    let metadata = fs::symlink_metadata(&packages)
        .map_err(|_| "Manim site-packages runtime is unavailable".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err("Manim site-packages runtime must be a real directory".into());
    }
    let canonical = fs::canonicalize(packages)
        .map_err(|_| "Manim site-packages runtime could not be canonicalized".to_string())?;
    if !canonical.starts_with(runtime_root) {
        return Err("Manim site-packages runtime escaped its delegated root".into());
    }
    Ok(canonical)
}

fn ensure_private_dir(root: &Path, name: &str) -> Result<PathBuf, String> {
    direct_component(name, "private runtime directory")?;
    let path = root.join(name);
    match fs::symlink_metadata(&path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                return Err("private runtime path must be a real directory".into());
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(&path)
                .map_err(|_| "private runtime directory could not be created".to_string())?;
        }
        Err(_) => return Err("private runtime directory metadata is unavailable".into()),
    }
    Ok(path)
}

fn command_environment(
    command: &mut Command,
    site_packages: &Path,
    ffmpeg: &Path,
    fontconfig_root: &Path,
    work_root: &Path,
) -> Result<(), String> {
    let ffmpeg_parent = ffmpeg
        .parent()
        .ok_or_else(|| "sealed FFmpeg has no parent directory".to_string())?;
    let cache = ensure_private_dir(work_root, ".cache")?;
    let mpl = ensure_private_dir(work_root, ".matplotlib")?;
    command.env_clear();
    command.env("PYTHONPATH", site_packages);
    command.env("PYTHONNOUSERSITE", "1");
    command.env("PYTHONDONTWRITEBYTECODE", "1");
    command.env("PATH", ffmpeg_parent);
    command.env("FONTCONFIG_PATH", fontconfig_root);
    command.env("FONTCONFIG_FILE", fontconfig_root.join("fonts.conf"));
    command.env("HOME", work_root);
    command.env("XDG_CACHE_HOME", cache);
    command.env("MPLCONFIGDIR", mpl);
    command.env("LANG", "C.UTF-8");
    command.env("LC_ALL", "C.UTF-8");
    Ok(())
}

fn checked_output(mut command: Command, label: &str) -> Result<Vec<u8>, String> {
    let output = command
        .output()
        .map_err(|_| format!("{label} could not be launched"))?;
    checked_output_result(output, label)
}

fn checked_output_result(output: std::process::Output, label: &str) -> Result<Vec<u8>, String> {
    if !output.status.success() {
        let stderr = &output.stderr[..output.stderr.len().min(MAX_CAPTURE_BYTES)];
        return Err(format!(
            "{label} failed with {}: {}",
            output.status,
            String::from_utf8_lossy(stderr)
        ));
    }
    if output.stdout.len() > MAX_CAPTURE_BYTES {
        return Err(format!("{label} stdout exceeds its diagnostic bound"));
    }
    Ok(output.stdout)
}

const GRAPH_PROFILE_CUSTODY_DIRECTORY: &str = ".graph-profile-probe-originals-v1";

fn checked_graph_profile_output(
    command: Command,
    label: &str,
    work_root: &Path,
) -> Result<Vec<u8>, String> {
    let directory = work_root.join(GRAPH_PROFILE_CUSTODY_DIRECTORY);
    match fs::symlink_metadata(&directory) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return checked_output(command, label);
        }
        Err(_) => return Err("fixed Graph probe custody directory is unavailable".into()),
        Ok(_) => {}
    }
    #[cfg(target_os = "linux")]
    {
        graph_probe_custody::capture(command, label, work_root, &directory)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (command, label);
        Err(
            "fixed Graph probe original custody requires Linux retained directory descriptors"
                .into(),
        )
    }
}

#[cfg(target_os = "linux")]
mod graph_probe_custody {
    use super::{MAX_CAPTURE_BYTES, Value, checked_output_result, json};
    use sha2::{Digest, Sha256};
    use std::{
        ffi::OsStr,
        fs::{self, File, Metadata, OpenOptions},
        io::{Read, Seek, SeekFrom, Write},
        os::{
            fd::AsRawFd,
            unix::{
                ffi::OsStrExt,
                fs::{MetadataExt, OpenOptionsExt},
                process::ExitStatusExt,
            },
        },
        path::{Path, PathBuf},
        process::{ChildStderr, ChildStdout, Command, ExitStatus, Output, Stdio},
        sync::mpsc,
        thread,
    };

    const MAX_INPUT_BYTES: usize = 128 * 1024;
    const FILE_NAMES: [&str; 6] = [
        "intent.json",
        "spawned.json",
        "stdout.bin",
        "stderr.bin",
        "exit.json",
        "terminal.json",
    ];

    #[derive(Clone, Copy, PartialEq, Eq)]
    struct Identity {
        device: u64,
        inode: u64,
        mode: u32,
        uid: u32,
        gid: u32,
    }

    fn identity(metadata: &Metadata) -> Identity {
        Identity {
            device: metadata.dev(),
            inode: metadata.ino(),
            mode: metadata.mode(),
            uid: metadata.uid(),
            gid: metadata.gid(),
        }
    }

    struct Directory {
        path: PathBuf,
        file: File,
        identity: Identity,
    }

    struct OriginalFile {
        name: &'static str,
        file: File,
        identity: Identity,
    }

    impl Directory {
        fn open(work: &Path, path: &Path) -> Result<Self, String> {
            let before =
                fs::symlink_metadata(path).map_err(|_| "Graph custody directory metadata")?;
            let owner = fs::symlink_metadata(work).map_err(|_| "Graph work directory metadata")?;
            if !before.is_dir()
                || before.file_type().is_symlink()
                || (before.mode() & 0o7777) != 0o700
                || before.uid() != owner.uid()
                || path.parent() != Some(work)
                || fs::canonicalize(path).map_err(|_| "Graph custody directory canonical path")?
                    != path
            {
                return Err("Graph custody requires the original private owner directory".into());
            }
            let file = Self::open_original(path, &before)?;
            let result = Self {
                path: path.to_owned(),
                file,
                identity: identity(&before),
            };
            result.verify()?;
            Ok(result)
        }

        fn open_original(path: &Path, before: &Metadata) -> Result<File, String> {
            // Enforce the file kind and refuse a symlink in the actual open.
            // Metadata checked before open cannot prevent replacement by a FIFO.
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
                .open(path)
                .map_err(|_| "Graph custody directory descriptor")?;
            let opened = file
                .metadata()
                .map_err(|_| "Graph custody descriptor metadata")?;
            if !opened.is_dir() || identity(&opened) != identity(before) {
                return Err("Graph custody directory changed before opening".into());
            }
            Ok(file)
        }

        fn verify(&self) -> Result<(), String> {
            let path = fs::symlink_metadata(&self.path)
                .map_err(|_| "Graph custody directory disappeared")?;
            let opened = self
                .file
                .metadata()
                .map_err(|_| "Graph custody directory descriptor failed")?;
            if !path.is_dir()
                || path.file_type().is_symlink()
                || identity(&path) != self.identity
                || identity(&opened) != self.identity
                || fs::canonicalize(&self.path).map_err(|_| "Graph custody directory moved")?
                    != self.path
            {
                return Err("Graph custody directory identity changed".into());
            }
            Ok(())
        }

        fn member_path(&self, name: &str) -> PathBuf {
            // The directory descriptor remains owned for the complete capture.
            // Fixed members are created relative to that descriptor, even if a
            // pathname is concurrently replaced. No user path is interpolated.
            PathBuf::from(format!("/proc/self/fd/{}/{}", self.file.as_raw_fd(), name))
        }

        fn create(&self, name: &'static str) -> Result<OriginalFile, String> {
            self.verify()?;
            if !FILE_NAMES.contains(&name) {
                return Err("Graph custody member is outside the closed inventory".into());
            }
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(self.member_path(name))
                .map_err(|_| {
                    format!("Graph custody {name} already exists or could not be reserved")
                })?;
            let opened = file
                .metadata()
                .map_err(|_| "Graph custody file descriptor metadata")?;
            let original = OriginalFile {
                name,
                file,
                identity: identity(&opened),
            };
            self.verify_member(&original)?;
            Ok(original)
        }

        fn verify_member(&self, original: &OriginalFile) -> Result<(), String> {
            self.verify()?;
            let path = fs::symlink_metadata(self.member_path(original.name))
                .map_err(|_| "Graph custody member disappeared")?;
            let opened = original
                .file
                .metadata()
                .map_err(|_| "Graph custody member descriptor failed")?;
            if !path.is_file()
                || path.file_type().is_symlink()
                || (path.mode() & 0o7777) != 0o600
                || path.uid() != self.identity.uid
                || path.nlink() != 1
                || opened.nlink() != 1
                || identity(&path) != original.identity
                || identity(&opened) != original.identity
                || path.len() != opened.len()
            {
                return Err("Graph custody member identity changed".into());
            }
            Ok(())
        }

        fn write_json(&self, original: &mut OriginalFile, value: &Value) -> Result<(), String> {
            self.verify_member(original)?;
            let mut bytes = serde_json::to_vec(value).map_err(|_| "Graph custody JSON encoding")?;
            bytes.push(b'\n');
            if bytes.len() > MAX_INPUT_BYTES {
                return Err("Graph custody JSON exceeds its closed budget".into());
            }
            original
                .file
                .write_all(&bytes)
                .map_err(|_| "Graph custody JSON write failed")?;
            original
                .file
                .sync_all()
                .map_err(|_| "Graph custody JSON sync failed")?;
            self.verify_bytes(original, &bytes)
        }

        fn verify_bytes(&self, original: &mut OriginalFile, expected: &[u8]) -> Result<(), String> {
            self.verify_member(original)?;
            let before = original
                .file
                .metadata()
                .map_err(|_| "Graph custody readback metadata")?;
            if before.len() != expected.len() as u64 {
                return Err("Graph custody written length differs from original bytes".into());
            }
            original
                .file
                .seek(SeekFrom::Start(0))
                .map_err(|_| "Graph custody readback seek")?;
            let mut readback = Vec::new();
            (&mut original.file)
                .take(expected.len() as u64 + 1)
                .read_to_end(&mut readback)
                .map_err(|_| "Graph custody readback failed")?;
            let after = original
                .file
                .metadata()
                .map_err(|_| "Graph custody final descriptor metadata")?;
            self.verify_member(original)?;
            if readback != expected
                || before.len() != after.len()
                || before.mtime() != after.mtime()
                || before.mtime_nsec() != after.mtime_nsec()
                || before.ctime() != after.ctime()
                || before.ctime_nsec() != after.ctime_nsec()
            {
                return Err("Graph custody original bytes changed during readback".into());
            }
            Ok(())
        }
    }

    fn bytes(value: &[u8]) -> Value {
        json!({"encoding":"hex","bytes":value.len(),
            "sha256":hex::encode(Sha256::digest(value)),"value":hex::encode(value)})
    }

    fn os(value: &OsStr) -> Value {
        json!({"platform_encoding":"unix_bytes","original":bytes(value.as_bytes())})
    }

    fn input(command: &Command) -> Result<Value, String> {
        let args = command.get_args().collect::<Vec<_>>();
        if args.len() != 3 || args[0] != OsStr::new("-c") {
            return Err("Graph custody requires the fixed Python command shape".into());
        }
        let value = json!({
            "schema":"motionwright.manim.fixed-graph-probe-stream-intent/1",
            "capture_scope":"fixed_graph_probe_python_only",
            "version_guard_streams_captured":false,
            "executable":os(command.get_program()),
            "argv":args.iter().map(|value| os(value)).collect::<Vec<_>>(),
            "compiled_program_sha256":hex::encode(Sha256::digest(args[1].as_bytes())),
            "cwd":command.get_current_dir().map(|path| os(path.as_os_str())),
            "environment":command.get_envs().map(|(name,value)| json!({
                "name":os(name),"value":value.map(os),
            })).collect::<Vec<_>>(),
            "stdin_mode":"null",
            "per_stream_retained_prefix_limit_bytes":MAX_CAPTURE_BYTES+1,
            "complete_stream_limit_bytes":MAX_CAPTURE_BYTES,
            "environment_mode":"cleared_then_explicit",
            "automatic_retry":false,
            "render_performed":false,
            "profile_admitted":false,
        });
        if serde_json::to_vec(&value)
            .map_err(|_| "Graph custody input JSON")?
            .len()
            > MAX_INPUT_BYTES
        {
            return Err("Graph custody input exceeds its closed budget".into());
        }
        Ok(value)
    }

    struct CapturedStream {
        original: OriginalFile,
        bytes: Vec<u8>,
        observed_bytes: u64,
        observed_sha256: String,
        eof_observed: bool,
        read_error: Option<String>,
        custody_error: Option<String>,
    }

    fn capture_stream(mut source: impl Read, mut original: OriginalFile) -> CapturedStream {
        let mut kept = Vec::new();
        let mut observed_bytes = 0_u64;
        let mut digest = Sha256::new();
        let mut eof_observed = false;
        let mut read_error = None;
        let mut custody_error = None;
        let mut chunk = [0_u8; 8192];
        loop {
            let length = match source.read(&mut chunk) {
                Ok(0) => {
                    eof_observed = true;
                    break;
                }
                Ok(length) => length,
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(error) => {
                    read_error = Some(error.to_string());
                    break;
                }
            };
            observed_bytes = observed_bytes.saturating_add(length as u64);
            digest.update(&chunk[..length]);
            let available = (MAX_CAPTURE_BYTES + 1)
                .saturating_sub(kept.len())
                .min(length);
            let retained = &chunk[..available];
            if custody_error.is_none()
                && let Err(error) = original.file.write_all(retained)
            {
                custody_error = Some(error.to_string());
            }
            kept.extend_from_slice(retained);
            // Drain excess bytes without retaining unbounded data or closing the
            // producer's pipe early. Overflow is explicit and can never pass G2.
        }
        if let Err(error) = original.file.sync_all() {
            custody_error.get_or_insert_with(|| error.to_string());
        }
        CapturedStream {
            original,
            bytes: kept,
            observed_bytes,
            observed_sha256: hex::encode(digest.finalize()),
            eof_observed,
            read_error,
            custody_error,
        }
    }

    fn stream_report(stream: &CapturedStream) -> Value {
        json!({
            "relative_path":stream.original.name,
            "observed_bytes":stream.observed_bytes,
            "observed_sha256":stream.observed_sha256,
            "retained_bytes":stream.bytes.len(),
            "retained_sha256":hex::encode(Sha256::digest(&stream.bytes)),
            "eof_observed":stream.eof_observed,
            "read_error":stream.read_error,
            "custody_error":stream.custody_error,
            "capture_limit_exceeded":stream.observed_bytes>MAX_CAPTURE_BYTES as u64,
            "original_complete":stream.eof_observed && stream.read_error.is_none()
                && stream.custody_error.is_none() && stream.observed_bytes<=MAX_CAPTURE_BYTES as u64,
        })
    }

    type ReaderCapture = Result<CapturedStream, String>;

    fn joined_capture(
        result: thread::Result<Option<CapturedStream>>,
        stream: &str,
    ) -> ReaderCapture {
        match result {
            Ok(Some(captured)) => Ok(captured),
            Ok(None) => Err(format!(
                "Graph {stream} reader did not receive the original pipe"
            )),
            Err(_) => Err(format!("Graph {stream} reader panicked")),
        }
    }

    fn reader_report(stream: &ReaderCapture) -> Value {
        match stream {
            Ok(captured) => stream_report(captured),
            Err(error) => json!({
                "reader_error":error,"original_complete":false,"eof_observed":false,
            }),
        }
    }

    fn captured_terminal(
        status: &Result<ExitStatus, String>,
        stdout: &ReaderCapture,
        stderr: &ReaderCapture,
        custody_errors: &[String],
    ) -> Value {
        let process_exit = status.as_ref().ok().map(|status| json!({
            "code":status.code(),"success":status.success(),"unix_wait_status":status.into_raw(),
        }));
        // Completeness of original streams does not imply a successful probe.
        let complete = status.is_ok()
            && [stdout, stderr].iter().all(|stream| {
                stream.as_ref().is_ok_and(|captured| {
                    captured.eof_observed
                        && captured.read_error.is_none()
                        && captured.custody_error.is_none()
                        && captured.observed_bytes <= MAX_CAPTURE_BYTES as u64
                })
            })
            && custody_errors.is_empty();
        let mut report = json!({
            "schema":"motionwright.manim.fixed-graph-probe-stream-terminal/1",
            "capture_scope":"fixed_graph_probe_python_only",
            "process_exit":process_exit,
            "stdout":reader_report(stdout),"stderr":reader_report(stderr),
            "original_complete":complete,"custody_errors":custody_errors,
            "automatic_retry":false,"render_performed":false,"profile_admitted":false,
        });
        if let Err(error) = status {
            report["original_error"] = json!(error);
        }
        report
    }

    fn captured_result(
        status: Result<ExitStatus, String>,
        stdout: ReaderCapture,
        stderr: ReaderCapture,
        label: &str,
    ) -> Result<Vec<u8>, String> {
        let status = status?;
        if !status.success() {
            // A known unsuccessful exit remains primary even if a reader failed.
            // Do not construct a synthetic Output with an invented empty stream.
            return match &stderr {
                Ok(captured) => {
                    let bytes = &captured.bytes[..captured.bytes.len().min(MAX_CAPTURE_BYTES)];
                    Err(format!(
                        "{label} failed with {status}: {}",
                        String::from_utf8_lossy(bytes)
                    ))
                }
                Err(error) => Err(format!(
                    "{label} failed with {status}; original stderr unavailable: {error}"
                )),
            };
        }
        let stdout =
            stdout.map_err(|error| format!("{label} exited with {status}, but {error}"))?;
        let stderr =
            stderr.map_err(|error| format!("{label} exited with {status}, but {error}"))?;
        if let Some(error) = &stdout.read_error {
            return Err(format!(
                "{label} stdout read failed after {status}: {error}"
            ));
        }
        if let Some(error) = &stderr.read_error {
            return Err(format!(
                "{label} stderr read failed after {status}: {error}"
            ));
        }
        if !stdout.eof_observed || !stderr.eof_observed {
            return Err(format!(
                "{label} exited with {status}, but original stream EOF was not observed"
            ));
        }
        checked_output_result(
            Output {
                status,
                stdout: stdout.bytes,
                stderr: stderr.bytes,
            },
            label,
        )
    }

    fn finish(
        original: Result<Vec<u8>, String>,
        custody_errors: &[String],
    ) -> Result<Vec<u8>, String> {
        match original {
            Err(error) => Err(error),
            Ok(stdout) if custody_errors.is_empty() => Ok(stdout),
            Ok(_) => Err("fixed Graph probe original custody is incomplete".into()),
        }
    }

    pub(super) fn capture(
        mut command: Command,
        label: &str,
        work: &Path,
        path: &Path,
    ) -> Result<Vec<u8>, String> {
        let request = input(&command)?;
        let root = Directory::open(work, path)?;
        // intent.json is the exclusive attempt reservation. Existing material is
        // never overwritten, removed, retried or silently reused by another probe.
        let mut intent = root.create("intent.json")?;
        root.write_json(&mut intent, &request)?;
        let mut spawned = root.create("spawned.json")?;
        let stdout_file = root.create("stdout.bin")?;
        let stderr_file = root.create("stderr.bin")?;
        let mut exit_file = root.create("exit.json")?;
        let mut terminal = root.create("terminal.json")?;
        let mut custody_errors = Vec::new();
        command
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let outcome = thread::scope(|scope| {
            // Prepare both readers before launching the only native child.
            let (out_tx, out_rx) = mpsc::sync_channel::<(ChildStdout, OriginalFile)>(1);
            let (err_tx, err_rx) = mpsc::sync_channel::<(ChildStderr, OriginalFile)>(1);
            let out = match thread::Builder::new()
                .name("graph-probe-stdout-custody".into())
                .spawn_scoped(scope, move || {
                    out_rx
                        .recv()
                        .ok()
                        .map(|(source, file)| capture_stream(source, file))
                }) {
                Ok(reader) => reader,
                Err(_) => {
                    drop(out_tx);
                    drop(err_tx);
                    return Err("Graph custody stdout reader could not be prepared".to_string());
                }
            };
            let err = match thread::Builder::new()
                .name("graph-probe-stderr-custody".into())
                .spawn_scoped(scope, move || {
                    err_rx
                        .recv()
                        .ok()
                        .map(|(source, file)| capture_stream(source, file))
                }) {
                Ok(reader) => reader,
                Err(_) => {
                    drop(out_tx);
                    drop(err_tx);
                    let _ = out.join();
                    return Err("Graph custody stderr reader could not be prepared".to_string());
                }
            };
            let mut child = match command.spawn() {
                Ok(child) => child,
                Err(_) => {
                    drop(out_tx);
                    drop(err_tx);
                    let _ = out.join();
                    let _ = err.join();
                    return Err(format!("{label} could not be launched"));
                }
            };
            if let Err(error) = root.write_json(
                &mut spawned,
                &json!({
                    "schema":"motionwright.manim.fixed-graph-probe-spawn/1",
                    "pid":child.id(),"automatic_retry":false,
                }),
            ) {
                custody_errors.push(error);
            }
            // A missing pipe or reader must not erase an observed child terminal.
            // Deliver independently and close both channels before either join.
            let stdout_delivered = match child.stdout.take() {
                Some(stdout) => out_tx.send((stdout, stdout_file)).is_ok(),
                None => {
                    drop(stdout_file);
                    false
                }
            };
            let stderr_delivered = match child.stderr.take() {
                Some(stderr) => err_tx.send((stderr, stderr_file)).is_ok(),
                None => {
                    drop(stderr_file);
                    false
                }
            };
            drop(out_tx);
            drop(err_tx);
            if !stdout_delivered || !stderr_delivered {
                custody_errors.push("Graph custody prepared reader was lost".to_string());
                let _ = child.kill();
            }
            let status = child
                .wait()
                .map_err(|_| format!("{label} could not be launched"));
            if let Ok(status) = &status
                && let Err(error) = root.write_json(
                    &mut exit_file,
                    &json!({
                        "schema":"motionwright.manim.fixed-graph-probe-exit/1",
                        "code":status.code(),"success":status.success(),
                        "unix_wait_status":status.into_raw(),
                        "stream_eof_not_implied":true,
                    }),
                )
            {
                custody_errors.push(error);
            }
            // Preserve both reader outcomes beside status; neither join may
            // return early and discard the already observed process exit.
            let stdout_result = out.join();
            let stderr_result = err.join();
            Ok::<_, String>((
                status,
                joined_capture(stdout_result, "stdout"),
                joined_capture(stderr_result, "stderr"),
            ))
        });
        let (status, mut stdout, mut stderr) = match outcome {
            Ok(value) => value,
            Err(original_error) => {
                // No EOF, exit or successful child output is manufactured here.
                let _ = root.write_json(
                    &mut terminal,
                    &json!({
                        "schema":"motionwright.manim.fixed-graph-probe-stream-terminal/1",
                        "original_complete":false,"original_error":original_error,
                        "custody_errors":custody_errors,"automatic_retry":false,
                        "render_performed":false,"profile_admitted":false,
                    }),
                );
                return Err(original_error);
            }
        };
        for captured in [&mut stdout, &mut stderr] {
            match captured {
                Ok(stream) => {
                    if let Err(error) = root.verify_bytes(&mut stream.original, &stream.bytes) {
                        custody_errors.push(error);
                    }
                    if stream.custody_error.is_some()
                        || stream.observed_bytes > MAX_CAPTURE_BYTES as u64
                    {
                        custody_errors.push(format!(
                            "Graph {} custody is incomplete",
                            stream.original.name
                        ));
                    }
                }
                Err(error) => custody_errors.push(error.clone()),
            }
        }
        let report = captured_terminal(&status, &stdout, &stderr, &custody_errors);
        if let Err(error) = root.write_json(&mut terminal, &report) {
            custody_errors.push(error);
        }
        let original = captured_result(status, stdout, stderr, label);
        finish(original, &custody_errors)
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::{
            io,
            os::unix::fs::{PermissionsExt, symlink},
        };

        fn root() -> (tempfile::TempDir, PathBuf, Directory) {
            let temp = tempfile::tempdir().unwrap();
            let work = fs::canonicalize(temp.path()).unwrap();
            let path = work.join(super::super::GRAPH_PROFILE_CUSTODY_DIRECTORY);
            fs::create_dir(&path).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            let root = Directory::open(&work, &path).unwrap();
            (temp, work, root)
        }

        #[test]
        fn raw_binary_streams_have_observed_eof_and_exact_originals() {
            let (_temp, _work, root) = root();
            for name in ["stdout.bin", "stderr.bin"] {
                let raw = b"\xff\0unparsed\n\xfe";
                let mut stream = capture_stream(&raw[..], root.create(name).unwrap());
                assert_eq!(stream.bytes, raw);
                assert_eq!(stream.observed_bytes, raw.len() as u64);
                assert!(stream.eof_observed);
                assert!(stream.read_error.is_none());
                assert!(stream.custody_error.is_none());
                root.verify_bytes(&mut stream.original, raw).unwrap();
                assert_eq!(fs::read(root.member_path(name)).unwrap(), raw);
                assert_eq!(stream_report(&stream)["original_complete"], true);
            }
        }

        #[test]
        fn overflow_is_bounded_and_drain_digest_is_not_misreported_as_original() {
            let (_temp, _work, root) = root();
            let raw = vec![0xa5; MAX_CAPTURE_BYTES + 31];
            let mut stream = capture_stream(&raw[..], root.create("stdout.bin").unwrap());
            assert!(stream.eof_observed);
            assert_eq!(stream.observed_bytes, raw.len() as u64);
            assert_eq!(stream.observed_sha256, hex::encode(Sha256::digest(&raw)));
            assert_eq!(stream.bytes.len(), MAX_CAPTURE_BYTES + 1);
            assert_eq!(stream.bytes, raw[..MAX_CAPTURE_BYTES + 1]);
            root.verify_bytes(&mut stream.original, &stream.bytes)
                .unwrap();
            let report = stream_report(&stream);
            assert_eq!(report["capture_limit_exceeded"], true);
            assert_eq!(report["original_complete"], false);
        }

        #[test]
        fn interrupted_reads_resume_but_failed_reads_never_manufacture_eof() {
            struct FailingReader(u8);
            impl Read for FailingReader {
                fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
                    self.0 += 1;
                    match self.0 {
                        1 | 3 => {
                            target[..3].copy_from_slice(b"a\0\xff");
                            Ok(3)
                        }
                        2 => Err(io::Error::from(io::ErrorKind::Interrupted)),
                        _ => Err(io::Error::from(io::ErrorKind::PermissionDenied)),
                    }
                }
            }
            let (_temp, _work, root) = root();
            let mut stream = capture_stream(FailingReader(0), root.create("stdout.bin").unwrap());
            assert_eq!(stream.bytes, b"a\0\xffa\0\xff");
            assert!(!stream.eof_observed);
            assert!(stream.read_error.is_some());
            assert_eq!(stream_report(&stream)["original_complete"], false);
            root.verify_bytes(&mut stream.original, &stream.bytes)
                .unwrap();
        }

        #[test]
        fn original_directories_members_and_bytes_cannot_be_rebound() {
            let (_temp, work, root) = root();
            assert!(root.create("not-in-the-inventory").is_err());
            let mut original = root.create("stdout.bin").unwrap();
            original.file.write_all(b"before").unwrap();
            original.file.sync_all().unwrap();
            root.verify_bytes(&mut original, b"before").unwrap();
            fs::write(root.member_path("stdout.bin"), b"edited").unwrap();
            assert!(root.verify_bytes(&mut original, b"before").is_err());
            fs::hard_link(root.member_path("stdout.bin"), work.join("alias")).unwrap();
            assert!(root.verify_member(&original).is_err());
            assert!(root.create("stdout.bin").is_err());

            let alias = work.join("linked-custody");
            symlink(&root.path, &alias).unwrap();
            assert!(Directory::open(&work, &alias).is_err());
            let retired = work.join("retired-custody");
            fs::rename(&root.path, &retired).unwrap();
            fs::create_dir(&root.path).unwrap();
            fs::set_permissions(&root.path, fs::Permissions::from_mode(0o700)).unwrap();
            assert!(root.verify().is_err());
            assert!(root.create("stderr.bin").is_err());
            assert!(!root.path.join("stderr.bin").exists());
            assert!(!retired.join("stderr.bin").exists());
        }

        #[test]
        fn original_process_error_takes_precedence_over_custody_failure() {
            let (_temp, _work, root) = root();
            let mut destination = root.create("stderr.bin").unwrap();
            destination.file = File::open(root.member_path("stderr.bin")).unwrap();
            let raw = b"original\xff";
            let stream = capture_stream(&raw[..], destination);
            assert_eq!(stream.bytes, raw);
            assert!(stream.eof_observed);
            assert!(stream.read_error.is_none());
            assert!(stream.custody_error.is_some());
            assert_eq!(fs::read(root.member_path("stderr.bin")).unwrap(), b"");
            assert_eq!(stream_report(&stream)["original_complete"], false);
            let errors = [stream.custody_error.unwrap()];
            let original =
                "fixed Graph profile probe failed with exit status: 7: original \u{fffd}";
            assert_eq!(
                finish(Err(original.to_owned()), &errors).unwrap_err(),
                original
            );
            assert!(finish(Ok(b"unparsed".to_vec()), &errors).is_err());
            assert_eq!(finish(Ok(b"unparsed".to_vec()), &[]).unwrap(), b"unparsed");
        }
        #[test]
        fn failed_child_spawn_closes_both_prepared_reader_channels() {
            let (_temp, work, root) = root();
            let path = root.path.clone();
            drop(root);
            let mut command = Command::new(work.join("absent-fixed-graph-executable"));
            command.args(["-c", "inert fixture"]).arg(&work);
            command.current_dir(&work).env_clear();
            let error = capture(command, "fixed Graph profile probe", &work, &path).unwrap_err();
            assert_eq!(error, "fixed Graph profile probe could not be launched");
            let terminal: Value =
                serde_json::from_slice(&fs::read(path.join("terminal.json")).unwrap()).unwrap();
            assert_eq!(terminal["original_complete"], false);
            assert_eq!(terminal["original_error"], error);
            assert!(!terminal.as_object().unwrap().contains_key("process_exit"));
            assert!(fs::read(path.join("spawned.json")).unwrap().is_empty());
            assert!(fs::read(path.join("exit.json")).unwrap().is_empty());
            assert!(fs::read(path.join("stdout.bin")).unwrap().is_empty());
            assert!(fs::read(path.join("stderr.bin")).unwrap().is_empty());
        }

        #[test]
        fn nonzero_exit_survives_original_stream_read_failure() {
            struct PrefixThenError(bool);
            impl Read for PrefixThenError {
                fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
                    if self.0 {
                        return Err(io::Error::from(io::ErrorKind::PermissionDenied));
                    }
                    self.0 = true;
                    target[..8].copy_from_slice(b"original");
                    Ok(8)
                }
            }
            let (_temp, _work, root) = root();
            let stdout = Ok(capture_stream(
                &b"unparsed"[..],
                root.create("stdout.bin").unwrap(),
            ));
            let stderr = Ok(capture_stream(
                PrefixThenError(false),
                root.create("stderr.bin").unwrap(),
            ));
            let status = Ok(ExitStatus::from_raw(7 << 8));
            let report = captured_terminal(&status, &stdout, &stderr, &[]);
            assert_eq!(report["process_exit"]["code"], 7);
            assert_eq!(report["process_exit"]["success"], false);
            assert_eq!(report["stderr"]["eof_observed"], false);
            assert!(report["stderr"]["read_error"].is_string());
            assert_eq!(report["stderr"]["observed_bytes"], 8);
            assert_eq!(report["original_complete"], false);
            let error =
                captured_result(status, stdout, stderr, "fixed Graph profile probe").unwrap_err();
            assert_eq!(
                error,
                "fixed Graph profile probe failed with exit status: 7: original"
            );
            assert_eq!(
                finish(Err(error.clone()), &["late custody failure".into()]).unwrap_err(),
                error
            );
        }

        #[test]
        fn panicked_reader_preserves_known_nonzero_process_terminal() {
            let (_temp, _work, root) = root();
            let joined = thread::spawn(|| -> Option<CapturedStream> {
                panic!("inert original reader failure; no native process");
            })
            .join();
            let stdout = joined_capture(joined, "stdout");
            let stderr = Ok(capture_stream(
                &b"original diagnostic"[..],
                root.create("stderr.bin").unwrap(),
            ));
            let status = Ok(ExitStatus::from_raw(9 << 8));
            let report = captured_terminal(&status, &stdout, &stderr, &[]);
            assert_eq!(report["process_exit"]["code"], 9);
            assert_eq!(
                report["stdout"]["reader_error"],
                "Graph stdout reader panicked"
            );
            assert_eq!(report["stdout"]["eof_observed"], false);
            assert!(report["stdout"].get("observed_bytes").is_none());
            assert_eq!(report["original_complete"], false);
            assert_eq!(
                captured_result(status, stdout, stderr, "fixed Graph profile probe").unwrap_err(),
                "fixed Graph profile probe failed with exit status: 9: original diagnostic"
            );
        }

        #[test]
        fn missing_reader_pipe_keeps_exit_and_does_not_invent_stderr() {
            let (_temp, _work, root) = root();
            let stdout = Ok(capture_stream(
                &b"unparsed"[..],
                root.create("stdout.bin").unwrap(),
            ));
            let stderr = joined_capture(Ok(None), "stderr");
            let status = Ok(ExitStatus::from_raw(11 << 8));
            let report = captured_terminal(&status, &stdout, &stderr, &[]);
            assert_eq!(report["process_exit"]["code"], 11);
            assert_eq!(report["stderr"]["eof_observed"], false);
            assert!(report["stderr"].get("observed_sha256").is_none());
            assert!(report["stderr"].get("retained_bytes").is_none());
            assert_eq!(report["original_complete"], false);
            let error =
                captured_result(status, stdout, stderr, "fixed Graph profile probe").unwrap_err();
            assert!(error.starts_with("fixed Graph profile probe failed with exit status: 11; original stderr unavailable:"));
            assert!(error.contains("did not receive the original pipe"));
            assert!(!error.contains("could not be launched"));
        }

        #[test]
        fn successful_exit_cannot_admit_a_missing_original_stream() {
            let (_temp, _work, root) = root();
            let stdout = joined_capture(Ok(None), "stdout");
            let stderr = Ok(capture_stream(&b""[..], root.create("stderr.bin").unwrap()));
            let status = Ok(ExitStatus::from_raw(0));
            let report = captured_terminal(&status, &stdout, &stderr, &[]);
            assert_eq!(report["process_exit"]["code"], 0);
            assert_eq!(report["process_exit"]["success"], true);
            assert_eq!(report["original_complete"], false);
            assert_eq!(report["profile_admitted"], false);
            assert!(report["stdout"].get("retained_sha256").is_none());
            let error =
                captured_result(status, stdout, stderr, "fixed Graph profile probe").unwrap_err();
            assert!(error.contains("exited with exit status: 0"));
            assert!(error.contains("did not receive the original pipe"));
        }

        #[test]
        fn unobserved_process_terminal_keeps_its_original_error() {
            let original = "original Host result UNKNOWN; no exit observed".to_owned();
            let status = Err(original.clone());
            let stdout = joined_capture(Ok(None), "stdout");
            let stderr = joined_capture(Ok(None), "stderr");
            let report = captured_terminal(&status, &stdout, &stderr, &[]);
            assert!(report["process_exit"].is_null());
            assert_eq!(report["original_error"], original);
            assert_eq!(report["original_complete"], false);
            assert_eq!(
                captured_result(status, stdout, stderr, "fixed Graph profile probe").unwrap_err(),
                original
            );
        }

        #[test]
        fn retained_directory_open_rejects_rebound_file_symlink_and_directory() {
            let (_temp, work, root) = root();
            let before = root.file.metadata().unwrap();
            let opened = Directory::open_original(&root.path, &before).unwrap();
            assert!(opened.metadata().unwrap().is_dir());
            drop(opened);
            let retired = work.join("retired-original-directory");
            fs::rename(&root.path, &retired).unwrap();
            fs::write(&root.path, b"not a directory").unwrap();
            assert!(Directory::open_original(&root.path, &before).is_err());
            fs::remove_file(&root.path).unwrap();
            symlink(&retired, &root.path).unwrap();
            assert!(Directory::open_original(&root.path, &before).is_err());
            fs::remove_file(&root.path).unwrap();
            fs::create_dir(&root.path).unwrap();
            fs::set_permissions(&root.path, fs::Permissions::from_mode(0o700)).unwrap();
            assert!(Directory::open_original(&root.path, &before).is_err());
        }

        #[test]
        fn directory_replaced_by_fifo_is_rejected_without_a_writer_or_blocking_open() {
            let (_temp, work, root) = root();
            let before = root.file.metadata().unwrap();
            let path = root.path.clone();
            fs::rename(&path, work.join("retired-original-directory")).unwrap();
            let c_path = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
            // SAFETY: the owned CString is NUL-terminated and stays alive for
            // this libc call; the FIFO is created only in this private fixture.
            assert_eq!(
                unsafe { libc::mkfifo(c_path.as_ptr(), 0o600) },
                0,
                "{}",
                io::Error::last_os_error()
            );
            let (tx, rx) = mpsc::sync_channel(1);
            let reader_path = path.clone();
            let worker = thread::spawn(move || {
                let _ = tx.send(Directory::open_original(&reader_path, &before));
            });
            let first = rx.recv_timeout(std::time::Duration::from_secs(5));
            if first.is_err() {
                // If O_DIRECTORY regresses, release that one blocked FIFO read
                // before failing the test. This is not another native dispatch.
                let unblock = OpenOptions::new()
                    .read(true)
                    .write(true)
                    .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW)
                    .open(&path)
                    .unwrap();
                let recovered = rx.recv_timeout(std::time::Duration::from_secs(5));
                drop(unblock);
                assert!(
                    recovered.is_ok(),
                    "FIFO regression cleanup did not release the reader"
                );
            }
            worker.join().unwrap();
            let result =
                first.expect("directory descriptor open blocked on a FIFO without a writer");
            assert!(result.is_err());
            fs::remove_file(path).unwrap();
        }
    }
}

const VERSION_PROBE_PROGRAM: &str = r#"import sys
sys.path[:] = [entry for entry in sys.path if entry]
import importlib.util
import json
from pathlib import Path
root = Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.find_spec("manim")
if spec is None:
    result = {"state": "package_not_found", "observed_version": None}
else:
    if spec.origin is None:
        raise RuntimeError("Manim module has no bounded file origin")
    try:
        Path(spec.origin).resolve(strict=True).relative_to(root)
    except ValueError:
        raise RuntimeError("Manim module origin is outside the declared runtime") from None
    import manim
    result = {"state": "observed", "observed_version": str(manim.__version__)}
sys.stdout.write(json.dumps(result, separators=(",", ":")))
"#;

#[derive(Debug)]
struct ProbeArgs {
    runtime_root: PathBuf,
    python: PathBuf,
    ffmpeg: PathBuf,
    fontconfig_root: PathBuf,
    work_root: PathBuf,
}

fn parse_probe_args(raw: Vec<String>) -> Result<ProbeArgs, String> {
    if raw.first().map(String::as_str) != Some("probe") || raw.len() != 11 {
        return Err("runner requires the exact bounded probe argument set".into());
    }
    let mut values = BTreeMap::new();
    for pair in raw[1..].as_chunks::<2>().0 {
        let key = pair[0].as_str();
        if !matches!(
            key,
            "--runtime-root"
                | "--python-sealed"
                | "--ffmpeg-sealed"
                | "--fontconfig-root"
                | "--work-root"
        ) {
            return Err("runtime probe received an unsupported option".into());
        }
        if values.insert(key, pair[1].as_str()).is_some() {
            return Err("runtime probe option was duplicated".into());
        }
    }
    let required = |name: &str| {
        values
            .get(name)
            .map(|value| PathBuf::from(*value))
            .ok_or_else(|| format!("runtime probe option {name} is missing"))
    };
    Ok(ProbeArgs {
        runtime_root: required("--runtime-root")?,
        python: required("--python-sealed")?,
        ffmpeg: required("--ffmpeg-sealed")?,
        fontconfig_root: required("--fontconfig-root")?,
        work_root: required("--work-root")?,
    })
}

fn parse_graph_profile_probe_args(mut raw: Vec<String>) -> Result<ProbeArgs, String> {
    if raw.first().map(String::as_str) != Some("probe-graph-profile") {
        return Err("runner requires the fixed Graph profile probe command".into());
    }
    raw[0] = "probe".into();
    parse_probe_args(raw)
}

fn probe_graph_profile(args: ProbeArgs) -> Result<Value, String> {
    let runtime_root = real_directory(&args.runtime_root, "Manim runtime root")?;
    let work_root = real_directory(&args.work_root, "Manim work root")?;
    let fontconfig_root = real_directory(&args.fontconfig_root, "Fontconfig root")?;
    if !fontconfig_root.join("fonts.conf").is_file() {
        return Err("Fontconfig root has no fonts.conf".into());
    }
    let python = sealed_executable(&args.python, "sealed Python")?;
    let ffmpeg = sealed_executable(&args.ffmpeg, "sealed FFmpeg")?;
    let site_packages = runtime_site_packages(&runtime_root)?;
    validate_runtime_version(
        &python,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &work_root,
    )?;
    let program = fixed_graph_profile_probe_program_v1()
        .map_err(|_| "fixed Graph probe source could not be prepared".to_string())?;
    let mut command = Command::new(&python);
    command.args(["-c", &program]).arg(&runtime_root);
    command.current_dir(&work_root);
    command_environment(
        &mut command,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &work_root,
    )?;
    let stdout = checked_graph_profile_output(command, "fixed Graph profile probe", &work_root)?;
    let observation = parse_fixed_graph_profile_observation_v1(&stdout)
        .map_err(|_| "fixed Graph native observation was rejected".to_string())?;
    serde_json::to_value(observation)
        .map_err(|_| "fixed Graph native observation could not be encoded".to_string())
}

fn parse_version_probe_stdout(stdout: &[u8]) -> Result<Option<String>, String> {
    if stdout.len() > 4096 {
        return Err("Manim version observation exceeds its bound".into());
    }
    let data: Value = serde_json::from_slice(stdout)
        .map_err(|_| "Manim version observation is malformed".to_string())?;
    let object = data
        .as_object()
        .ok_or_else(|| "Manim version observation must be an object".to_string())?;
    if object.len() != 2 {
        return Err("Manim version observation shape is unsupported".into());
    }
    match data.get("state").and_then(Value::as_str) {
        Some("observed") => {
            let version = data
                .get("observed_version")
                .and_then(Value::as_str)
                .ok_or_else(|| "Manim observed version is missing".to_string())?;
            if version.is_empty()
                || version.len() > 80
                || version
                    .chars()
                    .any(|ch| ch.is_control() || ch.is_whitespace())
            {
                return Err("Manim observed version is out of bounds".into());
            }
            Ok(Some(version.to_owned()))
        }
        Some("package_not_found") if data.get("observed_version") == Some(&Value::Null) => Ok(None),
        _ => Err("Manim version observation state is unsupported".into()),
    }
}

fn observe_runtime_version(
    python: &Path,
    site_packages: &Path,
    ffmpeg: &Path,
    fontconfig_root: &Path,
    work_root: &Path,
) -> Result<Option<String>, String> {
    let mut command = Command::new(python);
    command
        .args(["-c", VERSION_PROBE_PROGRAM])
        .arg(site_packages);
    command.current_dir(work_root);
    command_environment(
        &mut command,
        site_packages,
        ffmpeg,
        fontconfig_root,
        work_root,
    )?;
    let stdout = checked_output(command, "Manim runtime version probe")?;
    parse_version_probe_stdout(&stdout)
}

fn probe_runtime(args: ProbeArgs) -> Result<Value, String> {
    let runtime_root = real_directory(&args.runtime_root, "Manim runtime root")?;
    let work_root = real_directory(&args.work_root, "Manim work root")?;
    let fontconfig_root = real_directory(&args.fontconfig_root, "Fontconfig root")?;
    if !fontconfig_root.join("fonts.conf").is_file() {
        return Err("Fontconfig root has no fonts.conf".into());
    }
    let python = sealed_executable(&args.python, "sealed Python")?;
    let ffmpeg = sealed_executable(&args.ffmpeg, "sealed FFmpeg")?;
    let site_packages = runtime_site_packages(&runtime_root)?;
    let observed = observe_runtime_version(
        &python,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &work_root,
    )?;
    let compatible = observed.as_deref().map(|version| version == MANIM_VERSION);
    let state = if observed.is_some() {
        "observed"
    } else {
        "package_not_found"
    };
    let reason = match compatible {
        Some(true) => "version_observed",
        Some(false) => "version_mismatch",
        None => "package_not_found_in_declared_runtime",
    };
    Ok(json!({
        "schema": 1,
        "operation": "runtime_version_probe",
        "requested": true,
        "source": "host_native_package_probe",
        "state": state,
        "expected_version": MANIM_VERSION,
        "observed_version": observed,
        "compatible": compatible,
        "reason_code": reason,
        "render_performed": false,
        "scene_mutation_performed": false,
    }))
}

fn validate_runtime_version(
    python: &Path,
    site_packages: &Path,
    ffmpeg: &Path,
    fontconfig_root: &Path,
    work_root: &Path,
) -> Result<(), String> {
    let observed =
        observe_runtime_version(python, site_packages, ffmpeg, fontconfig_root, work_root)?
            .ok_or_else(|| "Manim package was not found in the declared runtime".to_string())?;
    if observed != MANIM_VERSION {
        return Err(format!(
            "Manim runtime version mismatch: expected {MANIM_VERSION}, observed {observed}"
        ));
    }
    Ok(())
}

fn validate_artifact(args: &RenderArgs, output_root: &Path) -> Result<PathBuf, String> {
    let stem = args
        .source
        .strip_suffix(".py")
        .ok_or_else(|| "generated Manim source has no .py suffix".to_string())?;
    let quality = format!("{}p{}", args.height, args.fps);
    let relative = PathBuf::from("videos")
        .join(stem)
        .join(quality)
        .join(format!("{}.mp4", args.output_name));
    if relative
        .components()
        .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err("Manim artifact path is not normalized".into());
    }
    let path = output_root.join(relative);
    let metadata = fs::symlink_metadata(&path)
        .map_err(|_| "Manim renderer did not publish the expected MP4".to_string())?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > MAX_VIDEO_BYTES
    {
        return Err("Manim MP4 is not a bounded regular file".into());
    }
    let canonical =
        fs::canonicalize(path).map_err(|_| "Manim MP4 could not be canonicalized".to_string())?;
    if !canonical.starts_with(output_root) {
        return Err("Manim MP4 escaped the owner output root".into());
    }
    Ok(canonical)
}

fn observe_artifact(
    python: &Path,
    site_packages: &Path,
    ffmpeg: &Path,
    fontconfig_root: &Path,
    work_root: &Path,
    source: &Path,
    artifact: &Path,
) -> Result<(), String> {
    let report = artifact.with_extension("mp4.decoded-video.json");
    let mut command = Command::new(python);
    command.args(["-c", include_str!("../native_video_observer.py")]);
    command
        .arg(artifact)
        .arg(source)
        .arg(&report)
        .current_dir(work_root);
    command_environment(
        &mut command,
        site_packages,
        ffmpeg,
        fontconfig_root,
        work_root,
    )?;
    let captured = checked_output(command, "Original native video observation")?;
    let value: serde_json::Value = serde_json::from_slice(&captured)
        .map_err(|_| "Native video observer returned malformed completion".to_string())?;
    if value.get("schema").and_then(serde_json::Value::as_str)
        != Some("motionwright.native-video-observation/1")
        || value.get("status").is_some()
        || !value
            .get("bytes")
            .and_then(serde_json::Value::as_u64)
            .is_some_and(|n| n > 0 && n <= 8 * 1024 * 1024)
    {
        return Err("Native video observer did not retain a complete report".into());
    }
    Ok(())
}

fn render(args: RenderArgs) -> Result<(), String> {
    let runtime_root = real_directory(&args.runtime_root, "Manim runtime root")?;
    let work_root = real_directory(&args.work_root, "Manim work root")?;
    let output_root = real_directory(&args.output_root, "Manim output root")?;
    let fontconfig_root = real_directory(&args.fontconfig_root, "Fontconfig root")?;
    if !fontconfig_root.join("fonts.conf").is_file() {
        return Err("Fontconfig root has no fonts.conf".into());
    }
    let python = sealed_executable(&args.python, "sealed Python")?;
    let ffmpeg = sealed_executable(&args.ffmpeg, "sealed FFmpeg")?;
    let site_packages = runtime_site_packages(&runtime_root)?;
    let source = source_file(&work_root, &args.source)?;
    validate_runtime_version(
        &python,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &work_root,
    )?;

    let mut command = Command::new(&python);
    // Fixed public Manim writer hook; original generated scene remains unchanged.
    // Runtime schema 2 is an owner-selected color policy, never a caller argv option.
    command.args([
        OsString::from("-c"),
        OsString::from(include_str!("../native_color_writer.py")),
        source.clone().into_os_string(),
        output_root.clone().into_os_string(),
        OsString::from(args.output_name.clone()),
        OsString::from(args.width.to_string()),
        OsString::from(args.height.to_string()),
        OsString::from(args.fps.to_string()),
        OsString::from("srgb-rgba8-opaque-v1"),
    ]);
    command.current_dir(&work_root);
    command_environment(
        &mut command,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &work_root,
    )?;
    // This fixed value comes only from the already validated owner runtime.
    // The generated graph uses it inside the same original Manim process.
    command.env("MOTIONWRIGHT_MANIM_RUNTIME_ROOT", &runtime_root);

    let status: ExitStatus = command
        .status()
        .map_err(|_| "Manim render process could not be launched".to_string())?;
    if !status.success() {
        return Err(format!("Manim Community render failed with {status}"));
    }
    let artifact = validate_artifact(&args, &output_root)?;
    // A decoder failure does not erase an already completed native render.
    // Its output remains unavailable for hybrid admission; no rerender occurs.
    let decoded_video_observed = observe_artifact(
        &python,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &work_root,
        &source,
        &artifact,
    )
    .is_ok();
    println!(
        "{}",
        json!({
            "runner": "motionwright-manim-runner",
            "manim_version": MANIM_VERSION,
            "artifact": artifact,
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "decoded_video_observed": decoded_video_observed
        })
    );
    Ok(())
}

fn main() {
    let raw = std::env::args().skip(1).collect::<Vec<_>>();
    if raw.first().map(String::as_str) == Some("probe-graph-profile") {
        let args = parse_graph_profile_probe_args(raw).unwrap_or_else(|error| fail(error));
        let report = probe_graph_profile(args).unwrap_or_else(|error| fail(error));
        print!("{report}");
        return;
    }
    if raw.first().map(String::as_str) == Some("probe") {
        let args = parse_probe_args(raw).unwrap_or_else(|error| fail(error));
        let report = probe_runtime(args).unwrap_or_else(|error| fail(error));
        println!("{report}");
        return;
    }
    let args = parse_render_args(raw).unwrap_or_else(|error| fail(error));
    if let Err(error) = render(args) {
        fail(error);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parser_rejects_extra_or_arbitrary_options() {
        let error =
            parse_render_args(vec!["render".into(), "--shell".into(), "sh".into()]).unwrap_err();
        assert!(error.contains("exact bounded") || error.contains("unsupported"));
    }

    #[test]
    fn direct_components_reject_traversal_and_paths() {
        for value in ["", ".", "..", "../x", "a/b", "a\\b"] {
            assert!(direct_component(value, "fixture").is_err());
        }
        assert!(direct_component("mw_0123456789abcdef", "fixture").is_ok());
    }
    fn probe_argv() -> Vec<String> {
        [
            "probe",
            "--runtime-root",
            "/runtime",
            "--python-sealed",
            "/python",
            "--ffmpeg-sealed",
            "/ffmpeg",
            "--fontconfig-root",
            "/fontconfig",
            "--work-root",
            "/work",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    }

    #[test]
    fn graph_profile_probe_closes_command_and_argument_selection() {
        let mut raw = probe_argv();
        raw[0] = "probe-graph-profile".into();
        assert!(parse_graph_profile_probe_args(raw.clone()).is_ok());
        for option in [
            "--source",
            "--output-root",
            "--shell",
            "--family",
            "--text",
            "--scene",
        ] {
            let mut bad = raw.clone();
            bad[1] = option.into();
            assert!(parse_graph_profile_probe_args(bad).is_err());
        }
        let mut duplicate = raw.clone();
        duplicate[3] = duplicate[1].clone();
        assert!(parse_graph_profile_probe_args(duplicate).is_err());
        let mut missing = raw.clone();
        missing.pop();
        assert!(parse_graph_profile_probe_args(missing).is_err());
        assert!(parse_probe_args(raw).is_err());
        assert!(parse_graph_profile_probe_args(probe_argv()).is_err());
    }

    #[test]
    fn runtime_probe_rejects_source_output_shell_and_duplicate_options() {
        assert!(parse_probe_args(probe_argv()).is_ok());
        for option in ["--source", "--output-root", "--shell", "--scene"] {
            let mut raw = probe_argv();
            raw[1] = option.into();
            assert!(parse_probe_args(raw).is_err());
        }
        let mut duplicate = probe_argv();
        duplicate[3] = "--runtime-root".into();
        assert!(parse_probe_args(duplicate).is_err());
        let mut extra = probe_argv();
        extra.extend(["--source".into(), "mw_untrusted.py".into()]);
        assert!(parse_probe_args(extra).is_err());
    }

    #[test]
    fn runtime_probe_parser_distinguishes_observed_and_package_not_found() {
        assert_eq!(
            parse_version_probe_stdout(br#"{"state":"observed","observed_version":"0.21.0"}"#)
                .unwrap(),
            Some("0.21.0".to_owned())
        );
        assert_eq!(
            parse_version_probe_stdout(br#"{"state":"observed","observed_version":"0.20.1"}"#)
                .unwrap(),
            Some("0.20.1".to_owned())
        );
        assert_eq!(
            parse_version_probe_stdout(br#"{"state":"package_not_found","observed_version":null}"#)
                .unwrap(),
            None
        );
        for value in [
            json!({"state":"observed","observed_version":null}),
            json!({"state":"observed","observed_version":"0.21.0\n"}),
            json!({"state":"observed","observed_version":"x".repeat(81)}),
            json!({"state":"package_not_found","observed_version":"0.21.0"}),
            json!({"state":"not_observed","observed_version":null}),
            json!({"state":"observed","observed_version":"0.21.0","injected":true}),
        ] {
            assert!(parse_version_probe_stdout(&serde_json::to_vec(&value).unwrap()).is_err());
        }
        assert!(parse_version_probe_stdout(&vec![b'x'; 4097]).is_err());
        assert!(parse_version_probe_stdout(b"\xff").is_err());
    }

    #[cfg(unix)]
    fn synthetic_probe_fixture(stdout: &str) -> (tempfile::TempDir, ProbeArgs) {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let runtime_root = temp.path().join("runtime");
        let site_packages = runtime_root.join("site-packages");
        let work_root = temp.path().join("work");
        let fontconfig_root = temp.path().join("fontconfig");
        fs::create_dir_all(&site_packages).unwrap();
        fs::create_dir(&work_root).unwrap();
        fs::create_dir(&fontconfig_root).unwrap();
        fs::write(
            runtime_root.join("runtime.json"),
            serde_json::to_vec(&json!({
                "schema": 2,
                "manim_version": MANIM_VERSION,
                "pyav_version": "15.0.0",
                "source_color_policy": "srgb-rgba8-opaque-v1",
                "color_recipe": "srgb-to-rec709-full12-v2",
            }))
            .unwrap(),
        )
        .unwrap();
        fs::write(fontconfig_root.join("fonts.conf"), b"synthetic fixture").unwrap();
        let python = temp.path().join("sealed-python");
        let ffmpeg = temp.path().join("sealed-ffmpeg");
        fs::write(&ffmpeg, b"synthetic sealed dependency").unwrap();
        let script = format!(
            "#!/bin/sh\nprintf 'invoked\\n' >> probe-invocations\nprintf '%s\\000' \"$@\" > probe-arguments\nprintf '%s' '{}'\n",
            stdout.replace('\'', "'\\''"),
        );
        fs::write(&python, script).unwrap();
        fs::set_permissions(&python, fs::Permissions::from_mode(0o700)).unwrap();
        (
            temp,
            ProbeArgs {
                runtime_root,
                python,
                ffmpeg,
                fontconfig_root,
                work_root,
            },
        )
    }

    #[cfg(unix)]
    #[test]
    fn synthetic_python_transport_observes_version_and_retains_render_guard() {
        for (version, expected_compatible) in [("0.21.0", true), ("0.20.1", false)] {
            let body =
                serde_json::to_string(&json!({"state":"observed", "observed_version":version}))
                    .unwrap();
            let (temp, args) = synthetic_probe_fixture(&body);
            let report = probe_runtime(args).unwrap();
            assert_eq!(report["observed_version"], version);
            assert_eq!(report["compatible"], expected_compatible);
            assert_eq!(report["render_performed"], false);
            assert_eq!(report["scene_mutation_performed"], false);
            let work = temp.path().join("work");
            assert_eq!(
                fs::read(work.join("probe-invocations")).unwrap(),
                b"invoked\n"
            );
            let argv = fs::read(work.join("probe-arguments")).unwrap();
            let parts: Vec<_> = argv
                .split(|byte| *byte == 0)
                .filter(|part| !part.is_empty())
                .collect();
            assert_eq!(parts.len(), 3);
            assert_eq!(parts[0], b"-c");
            assert_eq!(parts[1], VERSION_PROBE_PROGRAM.as_bytes());
            let packages = fs::canonicalize(temp.path().join("runtime/site-packages")).unwrap();
            assert_eq!(parts[2], packages.to_str().unwrap().as_bytes());
            let guard = validate_runtime_version(
                &temp.path().join("sealed-python"),
                &packages,
                &temp.path().join("sealed-ffmpeg"),
                &temp.path().join("fontconfig"),
                &work,
            );
            assert_eq!(guard.is_ok(), expected_compatible);
            assert_eq!(
                fs::read(work.join("probe-invocations")).unwrap(),
                b"invoked\ninvoked\n"
            );
            assert!(!temp.path().join("output").exists());
            assert!(fs::read_dir(&work).unwrap().all(|entry| {
                !matches!(
                    entry
                        .unwrap()
                        .path()
                        .extension()
                        .and_then(|value| value.to_str()),
                    Some("py" | "mp4")
                )
            }));
        }
    }

    #[cfg(unix)]
    #[test]
    fn synthetic_python_transport_reports_package_not_found_without_installing_or_rendering() {
        let (temp, args) =
            synthetic_probe_fixture(r#"{"state":"package_not_found","observed_version":null}"#);
        let report = probe_runtime(args).unwrap();
        assert_eq!(report["state"], "package_not_found");
        assert_eq!(report["observed_version"], Value::Null);
        assert_eq!(report["compatible"], Value::Null);
        assert_eq!(report["render_performed"], false);
        assert_eq!(report["scene_mutation_performed"], false);
        assert_eq!(
            fs::read(temp.path().join("work/probe-invocations")).unwrap(),
            b"invoked\n"
        );
        assert!(!temp.path().join("output").exists());
    }
    #[cfg(target_os = "linux")]
    fn graph_custody_command(args: &ProbeArgs) -> Command {
        // Reuses the existing inert synthetic transport fixture. It is not a
        // Python/Manim execution and does not establish native profile readiness.
        let mut command = Command::new(&args.python);
        command
            .args(["-c", "synthetic fixed Graph fixture"])
            .arg(&args.runtime_root);
        command.current_dir(&args.work_root).env_clear();
        command
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn graph_custody_is_opt_in_and_records_the_same_child_binary_pipes() {
        use sha2::{Digest, Sha256};
        use std::os::unix::fs::PermissionsExt;
        for enabled in [false, true] {
            let (_temp, mut args) = synthetic_probe_fixture("unused");
            args.work_root = fs::canonicalize(&args.work_root).unwrap();
            fs::write(&args.python, b"#!/bin/sh\nprintf 'invoked\\n' >> probe-invocations\nprintf '\\377\\000A\\n'\nprintf '\\376\\000B\\n' >&2\n").unwrap();
            let directory = args.work_root.join(GRAPH_PROFILE_CUSTODY_DIRECTORY);
            if enabled {
                fs::create_dir(&directory).unwrap();
                fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).unwrap();
            }
            let stdout = checked_graph_profile_output(
                graph_custody_command(&args),
                "fixed Graph profile probe",
                &args.work_root,
            )
            .unwrap();
            assert_eq!(stdout, b"\xff\0A\n");
            assert_eq!(
                fs::read(args.work_root.join("probe-invocations")).unwrap(),
                b"invoked\n"
            );
            if !enabled {
                assert!(!directory.exists());
                continue;
            }
            assert_eq!(
                fs::read(directory.join("stdout.bin")).unwrap(),
                b"\xff\0A\n"
            );
            assert_eq!(
                fs::read(directory.join("stderr.bin")).unwrap(),
                b"\xfe\0B\n"
            );
            let terminal: Value =
                serde_json::from_slice(&fs::read(directory.join("terminal.json")).unwrap())
                    .unwrap();
            assert_eq!(terminal["original_complete"], true);
            assert_eq!(terminal["process_exit"]["code"], 0);
            assert_eq!(terminal["stdout"]["eof_observed"], true);
            assert_eq!(terminal["stderr"]["eof_observed"], true);
            assert_eq!(
                terminal["stdout"]["retained_sha256"],
                hex::encode(Sha256::digest(b"\xff\0A\n"))
            );
            assert_eq!(
                terminal["stderr"]["retained_sha256"],
                hex::encode(Sha256::digest(b"\xfe\0B\n"))
            );
            let request: Value =
                serde_json::from_slice(&fs::read(directory.join("intent.json")).unwrap()).unwrap();
            assert_eq!(
                request["argv"][1]["original"]["value"],
                hex::encode(b"synthetic fixed Graph fixture")
            );
            assert_eq!(request["version_guard_streams_captured"], false);
            assert_eq!(request["profile_admitted"], false);
            assert_eq!(fs::read_dir(&directory).unwrap().count(), 6);
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn graph_custody_reservation_refuses_a_second_child_dispatch() {
        use std::os::unix::fs::PermissionsExt;
        let (_temp, mut args) = synthetic_probe_fixture("original");
        args.work_root = fs::canonicalize(&args.work_root).unwrap();
        let directory = args.work_root.join(GRAPH_PROFILE_CUSTODY_DIRECTORY);
        fs::create_dir(&directory).unwrap();
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(
            checked_graph_profile_output(
                graph_custody_command(&args),
                "fixed Graph profile probe",
                &args.work_root,
            )
            .unwrap(),
            b"original"
        );
        let intent = fs::read(directory.join("intent.json")).unwrap();
        let terminal = fs::read(directory.join("terminal.json")).unwrap();
        assert!(
            checked_graph_profile_output(
                graph_custody_command(&args),
                "fixed Graph profile probe",
                &args.work_root,
            )
            .is_err()
        );
        assert_eq!(
            fs::read(args.work_root.join("probe-invocations")).unwrap(),
            b"invoked\n"
        );
        assert_eq!(fs::read(directory.join("intent.json")).unwrap(), intent);
        assert_eq!(fs::read(directory.join("terminal.json")).unwrap(), terminal);
    }
}
