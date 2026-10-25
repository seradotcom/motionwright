//! Bounded Host-mediated launcher for the Motionwright Manim Community profile.
//!
//! Driver Host supplies exact sealed Python/FFmpeg dependencies and owner-granted
//! runtime/work/output roots. This runner owns the only Python module invocation;
//! project/model data can never select an executable, module, source program, shell
//! fragment, environment variable, or arbitrary Manim CLI option.

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
            serde_json::to_vec(&json!({"schema":1,"manim_version":MANIM_VERSION})).unwrap(),
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
}
