//! Bounded Host-mediated launcher for the Motionwright Manim Community profile.
//!
//! Driver Host supplies exact sealed Python/FFmpeg dependencies and owner-granted
//! runtime/work/output roots. This runner owns the only Python module invocation;
//! project/model data can never select an executable, module, source program, shell
//! fragment, environment variable, or arbitrary Manim CLI option.

use motionwright_manim_profile::{
    MAX_SOURCE_ENTRY_BYTES_V2, ManimRenderProfile, ManimStartPlan,
    inspect_native_source_readback_v2,
};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    ffi::OsString,
    fs::{self, OpenOptions},
    io::{Read, Write},
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
    plan: String,
    plan_sha256: String,
    source_sha256: String,
    scene: String,
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

fn bounded_regular_bytes(root: &Path, name: &str) -> Result<Vec<u8>, String> {
    direct_component(name, "Manim source member")?;
    let path = root.join(name);
    let meta = fs::symlink_metadata(&path).map_err(|_| "Manim source member is unavailable")?;
    if meta.file_type().is_symlink()
        || !meta.is_file()
        || meta.len() == 0
        || meta.len() > MAX_SOURCE_ENTRY_BYTES_V2 as u64
    {
        return Err("Manim source member is not a bounded regular file".into());
    }
    let canonical =
        fs::canonicalize(path).map_err(|_| "Manim source member cannot be canonicalized")?;
    if !canonical.starts_with(root) {
        return Err("Manim source member escaped its owner root".into());
    }
    let mut data = Vec::new();
    fs::File::open(canonical)
        .map_err(|_| "Manim source member cannot be opened")?
        .take(MAX_SOURCE_ENTRY_BYTES_V2 as u64 + 1)
        .read_to_end(&mut data)
        .map_err(|_| "Manim source member could not be read")?;
    if data.len() as u64 != meta.len() {
        return Err("Manim source member changed during observation".into());
    }
    Ok(data)
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(path)
        .map_err(|_| "Manim private source destination already exists or cannot be created")?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| "Manim private source cannot be retained".into())
}

struct PreparedSource {
    root: PathBuf,
    path: PathBuf,
    plan: ManimStartPlan,
    trusted_source: Vec<u8>,
}

fn prepare_trusted_source(args: &RenderArgs, work_root: &Path) -> Result<PreparedSource, String> {
    let document = bounded_regular_bytes(work_root, &args.plan)?;
    if sha256(&document) != args.plan_sha256 {
        return Err("Manim source plan changed before execution".into());
    }
    let plan: ManimStartPlan = serde_json::from_slice(&document)
        .map_err(|_| "Manim source plan is not a strict supported schema")?;
    plan.validate_for_profile(ManimRenderProfile {
        width: args.width,
        height: args.height,
        frame_rate: args.fps,
    })
    .map_err(|error| error.to_string())?;
    if plan.plan_document().map_err(|error| error.to_string())? != document {
        return Err(
            "Manim source plan document encoding differs from its versioned contract".into(),
        );
    }
    let trusted_source = plan
        .compile()
        .map_err(|error| error.to_string())?
        .into_bytes();
    if sha256(&trusted_source) != args.source_sha256
        || bounded_regular_bytes(work_root, &args.source)? != trusted_source
    {
        return Err("Manim source does not match the trusted compiler projection".into());
    }
    // Regenerate into a new private directory. The imported source bytes never
    // select the program executed by Python, nor can an old attempt be reused.
    let root = work_root.join(format!("{}_execution", args.output_name));
    fs::create_dir(&root)
        .map_err(|_| "Manim original execution directory already exists or cannot be created")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))
            .map_err(|_| "Manim execution directory permissions cannot be set")?;
    }
    let root = real_directory(&root, "Manim original execution directory")?;
    let path = root.join(&args.source);
    write_new(&path, &trusted_source)?;
    Ok(PreparedSource {
        root,
        path,
        plan,
        trusted_source,
    })
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
    if rest.len() != 30 {
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
                | "--plan"
                | "--plan-sha256"
                | "--source-sha256"
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
    let stem = source
        .strip_suffix(".py")
        .ok_or("Manim source needs its .py suffix")?;
    if stem != output_name
        || stem.len() != 35
        || !stem.starts_with("mw_")
        || !stem[3..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("Manim source identity does not match the generated output identity".into());
    }
    let plan = required("--plan")?;
    if plan != format!("{stem}.plan.json") {
        return Err("Manim plan filename does not match its source".into());
    }
    let plan_sha256 = required("--plan-sha256")?;
    let source_sha256 = required("--source-sha256")?;
    for digest in [&plan_sha256, &source_sha256] {
        if digest.len() != 64
            || !digest
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err("Manim source digest is malformed".into());
        }
    }

    Ok(RenderArgs {
        runtime_root: PathBuf::from(required("--runtime-root")?),
        python: PathBuf::from(required("--python-sealed")?),
        ffmpeg: PathBuf::from(required("--ffmpeg-sealed")?),
        fontconfig_root: PathBuf::from(required("--fontconfig-root")?),
        work_root: PathBuf::from(required("--work-root")?),
        output_root: PathBuf::from(required("--output-root")?),
        source,
        plan,
        plan_sha256,
        source_sha256,
        scene,
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
    if receipt.schema != 1 || receipt.manim_version != MANIM_VERSION {
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
    let temporary = ensure_private_dir(work_root, ".tmp")?;
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
    command.env("TMPDIR", &temporary);
    command.env("TMP", &temporary);
    command.env("TEMP", &temporary);
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

fn validate_runtime_version(
    python: &Path,
    site_packages: &Path,
    ffmpeg: &Path,
    fontconfig_root: &Path,
    work_root: &Path,
) -> Result<(), String> {
    let mut command = Command::new(python);
    command.args([
        "-c",
        "import manim,sys;sys.stdout.write(str(manim.__version__))",
    ]);
    command.current_dir(work_root);
    command_environment(
        &mut command,
        site_packages,
        ffmpeg,
        fontconfig_root,
        work_root,
    )?;
    let stdout = checked_output(command, "Manim runtime version probe")?;
    let observed = String::from_utf8(stdout)
        .map_err(|_| "Manim runtime version probe returned non-UTF-8".to_string())?;
    if observed.trim() != MANIM_VERSION {
        return Err(format!(
            "Manim runtime version mismatch: expected {MANIM_VERSION}, observed {}",
            observed.trim()
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
    let prepared = prepare_trusted_source(&args, &work_root)?;
    validate_runtime_version(
        &python,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &prepared.root,
    )?;

    let mut command = Command::new(&python);
    command.args([
        OsString::from("-m"),
        OsString::from("manim"),
        OsString::from("render"),
        OsString::from("--disable_caching"),
        OsString::from("--seed"),
        OsString::from("0"),
        OsString::from("--verbosity"),
        OsString::from("warning"),
        OsString::from("--progress_bar"),
        OsString::from("none"),
        OsString::from("--renderer"),
        OsString::from("cairo"),
        OsString::from("--format"),
        OsString::from("mp4"),
        OsString::from("--media_dir"),
        output_root.clone().into_os_string(),
        OsString::from("--resolution"),
        OsString::from(format!("{},{}", args.width, args.height)),
        OsString::from("--fps"),
        OsString::from(args.fps.to_string()),
        OsString::from("--output_file"),
        OsString::from(args.output_name.clone()),
        prepared.path.clone().into_os_string(),
        OsString::from(args.scene.clone()),
    ]);
    command.current_dir(&prepared.root);
    command_environment(
        &mut command,
        &site_packages,
        &ffmpeg,
        &fontconfig_root,
        &prepared.root,
    )?;

    let status: ExitStatus = command
        .status()
        .map_err(|_| "Manim render process could not be launched".to_string())?;
    if !status.success() {
        return Err(format!("Manim Community render failed with {status}"));
    }
    validate_artifact(&args, &output_root)?;
    if bounded_regular_bytes(&work_root, &args.source)? != prepared.trusted_source
        || bounded_regular_bytes(&prepared.root, &args.source)? != prepared.trusted_source
        || sha256(&bounded_regular_bytes(&work_root, &args.plan)?) != args.plan_sha256
    {
        return Err("Manim source custody changed during the original render".into());
    }
    let native_source_readback = match &prepared.plan {
        ManimStartPlan::V1(_) => None,
        ManimStartPlan::V2(plan) => {
            let name = format!("{}.objects.json", args.output_name);
            let bytes = bounded_regular_bytes(&prepared.root, &name)?;
            inspect_native_source_readback_v2(plan, &bytes).map_err(|error| error.to_string())?;
            write_new(&output_root.join(&name), &bytes)?;
            Some(
                json!({"relative_path":name,"sha256":sha256(&bytes),"bytes":bytes.len(),"media_type":"application/json"}),
            )
        }
    };
    println!(
        "MOTIONWRIGHT_MANIM_RUNNER_RESULT={}",
        json!({
            "schema_version": 2,
            "runner": "motionwright-manim-runner",
            "manim_version": MANIM_VERSION,
            "plan_sha256": args.plan_sha256,
            "source_sha256": args.source_sha256,
            "executed_source_sha256": sha256(&prepared.trusted_source),
            "width": args.width, "height": args.height, "fps": args.fps,
            "native_source_readback": native_source_readback
        })
    );
    Ok(())
}

fn main() {
    let raw = std::env::args().skip(1).collect::<Vec<_>>();
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
    struct SourceTestDirectory(PathBuf);
    impl SourceTestDirectory {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "motionwright-manim-source-test-{}",
                uuid::Uuid::now_v7()
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for SourceTestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn source_fixture(root: &Path) -> RenderArgs {
        use motionwright_manim_profile::{ManimPrimitive, ManimScenePlan};
        let plan = ManimStartPlan::V1(ManimScenePlan {
            project_id: uuid::Uuid::from_u128(1),
            generation: uuid::Uuid::from_u128(2),
            revision: 3,
            scene_id: uuid::Uuid::from_u128(4),
            primitives: vec![ManimPrimitive::Rectangle {
                node_id: uuid::Uuid::from_u128(5),
                x: 0.0,
                y: 0.0,
                width: 2.0,
                height: 1.0,
                color: "#E02040".into(),
            }],
        });
        let document = plan.plan_document().unwrap();
        let source = plan.compile().unwrap().into_bytes();
        let stem = "mw_0123456789abcdef0123456789abcdef";
        write_new(&root.join(format!("{stem}.py")), &source).unwrap();
        write_new(&root.join(format!("{stem}.plan.json")), &document).unwrap();
        RenderArgs {
            runtime_root: root.into(),
            python: root.join("not-executed-python"),
            ffmpeg: root.join("not-executed-ffmpeg"),
            fontconfig_root: root.into(),
            work_root: root.into(),
            output_root: root.into(),
            source: format!("{stem}.py"),
            plan: format!("{stem}.plan.json"),
            plan_sha256: sha256(&document),
            source_sha256: sha256(&source),
            scene: SCENE_CLASS.into(),
            output_name: stem.into(),
            width: 640,
            height: 360,
            fps: 12,
        }
    }

    #[test]
    fn runner_regenerates_a_new_private_program_without_executing_input_source() {
        let folder = SourceTestDirectory::new();
        let args = source_fixture(&folder.0);
        let prepared = prepare_trusted_source(&args, &folder.0).unwrap();
        assert_ne!(prepared.path, folder.0.join(&args.source));
        assert_eq!(
            fs::read(&prepared.path).unwrap(),
            prepared.plan.compile().unwrap().as_bytes()
        );
        assert_eq!(
            fs::read(folder.0.join(&args.source)).unwrap(),
            prepared.trusted_source
        );
        assert!(prepare_trusted_source(&args, &folder.0).is_err());
    }

    #[test]
    fn runner_rejects_rehashed_program_and_plan_drift_before_creating_execution_directory() {
        for case in 0..3 {
            let folder = SourceTestDirectory::new();
            let mut args = source_fixture(&folder.0);
            if case == 0 {
                let malicious = b"raise RuntimeError('untrusted input source')\n";
                fs::write(folder.0.join(&args.source), malicious).unwrap();
                args.source_sha256 = sha256(malicious);
            } else if case == 1 {
                let mut plan = fs::read(folder.0.join(&args.plan)).unwrap();
                plan.push(b' ');
                fs::write(folder.0.join(&args.plan), &plan).unwrap();
                args.plan_sha256 = sha256(&plan);
            } else {
                args.plan_sha256 = "0".repeat(64);
            }
            assert!(prepare_trusted_source(&args, &folder.0).is_err());
            assert!(
                !folder
                    .0
                    .join(format!("{}_execution", args.output_name))
                    .exists()
            );
        }
    }
}
