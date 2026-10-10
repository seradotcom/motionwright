//! Fixed Host-mediated launcher. It never accepts a source program, URL, shell or arbitrary argv.
use motionwright_hyperframes_driver::{
    file_sha, hex_digest, invalid, read, regular, root, sha, token, write_new,
};
use motionwright_hyperframes_profile::{HyperframesPlan, compile_html, validate_plan};
use semwright_types::Result;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

const AUTHOR: &str = include_str!("../../../../runtime/hyperframes/authoring.js");
const CAPTURE: &str = include_str!("../../../../runtime/hyperframes/capture.mjs");
const SANDBOX_POLICY: &str = include_str!("../../../../runtime/hyperframes/sandbox_policy.mjs");
const MAX_OUTPUT: u64 = 512 * 1024 * 1024;
#[derive(Debug)]
struct Args {
    runtime: PathBuf,
    node: PathBuf,
    ffmpeg: PathBuf,
    chromium: PathBuf,
    work: PathBuf,
    output: PathBuf,
    assets: PathBuf,
    job: String,
    plan_sha: String,
    source_sha: String,
    frame_window: Option<(u32, u32)>,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeFile {
    path: String,
    sha256: String,
    bytes: u64,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeReceipt {
    inventory: RuntimeInventoryReceipt,
    schema: u32,
    hyperframes: String,
    gsap: String,
    playwright: String,
    fontkit: String,
    npm_lock_sha256: String,
    capture_profile: String,
    platform: String,
    architecture: String,
    files: BTreeMap<String, RuntimeFile>,
    package_rights: String,
    sandbox: String,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeInventoryReceipt {
    path: String,
    sha256: String,
    files: usize,
    bytes: u64,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeInventory {
    schema: u32,
    files: Vec<RuntimeFile>,
}

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
struct Artifact {
    relative_path: String,
    sha256: String,
    bytes: u64,
    media_type: &'static str,
}
fn parse(raw: &[String]) -> Result<Args> {
    let window_mode = raw.first().map(String::as_str) == Some("render-window");
    if !(raw.first().map(String::as_str) == Some("render") && raw.len() == 21
        || window_mode && raw.len() == 25)
    {
        return Err(invalid(
            "Runner requires the exact bounded render or render-window argument set",
        ));
    }
    let mut values = BTreeMap::new();
    for pair in raw[1..].as_chunks::<2>().0 {
        if ![
            "--runtime-root",
            "--node-sealed",
            "--ffmpeg-sealed",
            "--chromium-sealed",
            "--work-root",
            "--output-root",
            "--assets-root",
            "--job",
            "--plan-sha256",
            "--source-sha256",
            "--first-frame",
            "--end-frame-exclusive",
        ]
        .contains(&pair[0].as_str())
            || values.insert(pair[0].clone(), pair[1].clone()).is_some()
        {
            return Err(invalid("Unsupported or duplicated runner option"));
        }
    }
    let get = |key: &str| {
        values
            .get(key)
            .cloned()
            .ok_or_else(|| invalid("Missing runner option"))
    };
    let job = get("--job")?;
    token(&job)?;
    let plan_sha = get("--plan-sha256")?;
    let source_sha = get("--source-sha256")?;
    if !hex_digest(&plan_sha) || !hex_digest(&source_sha) {
        return Err(invalid("Expected digests are malformed"));
    }
    let frame_window = if window_mode {
        let first_raw = get("--first-frame")?;
        let end_raw = get("--end-frame-exclusive")?;
        let first = first_raw
            .parse::<u32>()
            .map_err(|_| invalid("Invalid frame window start"))?;
        let end = end_raw
            .parse::<u32>()
            .map_err(|_| invalid("Invalid frame window end"))?;
        if first.to_string() != first_raw
            || end.to_string() != end_raw
            || first >= end
            || end > motionwright_hyperframes_profile::MAX_FRAMES
        {
            return Err(invalid(
                "Frame window must be a canonical bounded nonempty half-open interval",
            ));
        }
        Some((first, end))
    } else {
        if values.len() != 10 {
            return Err(invalid(
                "Full render cannot accept ungranted frame window options",
            ));
        }
        None
    };
    Ok(Args {
        runtime: PathBuf::from(get("--runtime-root")?),
        node: PathBuf::from(get("--node-sealed")?),
        ffmpeg: PathBuf::from(get("--ffmpeg-sealed")?),
        chromium: PathBuf::from(get("--chromium-sealed")?),
        work: PathBuf::from(get("--work-root")?),
        output: PathBuf::from(get("--output-root")?),
        assets: PathBuf::from(get("--assets-root")?),
        job,
        plan_sha,
        source_sha,
        frame_window,
    })
}
fn executable(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !path.is_absolute() || metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(invalid(
            "Executable must be an absolute Host-sealed regular file",
        ));
    }
    Ok(())
}
fn validate_runtime(runtime: &Path) -> Result<RuntimeReceipt> {
    let bytes = read(runtime, "runtime.json", 1024 * 1024)?;
    let receipt: RuntimeReceipt = serde_json::from_slice(&bytes)?;
    if receipt.schema != 1
        || receipt.hyperframes != "0.8.143"
        || receipt.gsap != "3.15.0"
        || receipt.playwright != "1.55.1"
        || receipt.fontkit != "2.0.4"
        || receipt.capture_profile != "hyperframes-core-chromium-png-v2"
        || receipt.platform != "linux"
        || receipt.architecture != "x64"
        || receipt.files.len() != 5
        || receipt.package_rights.is_empty()
        || receipt.sandbox.is_empty()
    {
        return Err(invalid(
            "Installed runtime differs from this exact Linux capture profile",
        ));
    }
    for name in [
        "hyperframes_script",
        "gsap_script",
        "sans_font",
        "mono_font",
        "browser",
    ] {
        let file = receipt
            .files
            .get(name)
            .ok_or_else(|| invalid("Runtime is missing a pinned dependency"))?;
        if !hex_digest(&file.sha256) {
            return Err(invalid("Runtime dependency digest is malformed"));
        }
        let limit = if name == "browser" {
            512 * 1024 * 1024
        } else {
            16 * 1024 * 1024
        };
        let (path, size) = regular(runtime, &file.path, limit).map_err(|error| {
            invalid(format!(
                "Runtime dependency {name} is invalid (declared {} bytes): {error}",
                file.bytes
            ))
        })?;
        if size != file.bytes || file_sha(&path, limit)? != file.sha256 {
            return Err(invalid("Pinned runtime dependency changed on disk"));
        }
    }
    if sha(&read(runtime, "package-lock.json", 2 * 1024 * 1024)?) != receipt.npm_lock_sha256 {
        return Err(invalid(
            "Runtime dependency lock changed after installation",
        ));
    }
    verify_runtime_inventory(runtime, &receipt)?;
    Ok(receipt)
}
fn child_environment(command: &mut Command, runtime: &Path, work: &Path) -> Result<()> {
    let temporary = work.join("tmp");
    fs::create_dir(&temporary)?;
    command
        .env_clear()
        .env("HOME", work)
        .env("XDG_CACHE_HOME", &temporary)
        .env("TMPDIR", &temporary)
        .env("LANG", "C.UTF-8")
        .env("LC_ALL", "C.UTF-8")
        .env("TZ", "UTC")
        .env("PLAYWRIGHT_BROWSERS_PATH", runtime.join(".browsers"));
    command.current_dir(work).stdin(Stdio::null());
    Ok(())
}
fn artifact(output: &Path, relative: &str, mime: &'static str, max: u64) -> Result<Artifact> {
    let (file, size) = regular(output, relative, max)?;
    Ok(Artifact {
        relative_path: relative.into(),
        sha256: file_sha(&file, max)?,
        bytes: size,
        media_type: mime,
    })
}
/// Bounded phase-only evidence for an owner-granted disposable job. Never logs
/// source, paths, environment, arguments, stderr or credentials.
fn phase(work: &Path, name: &'static str) -> Result<()> {
    let allowed = [
        "roots",
        "runtime",
        "document",
        "source",
        "launcher",
        "capture",
        "capture_nonzero",
        "encoder",
        "encoder_nonzero",
        "finished",
    ];
    if !allowed.contains(&name) {
        return Err(invalid("Unrecognized native phase identifier"));
    }
    let file = work.join("native-run-phases.txt");
    let mut stream = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(file)?;
    stream.write_all(name.as_bytes())?;
    stream.write_all(b"\n")?;
    stream.sync_data()?;
    Ok(())
}
fn render(args: Args) -> Result<()> {
    let runtime = root(&args.runtime)?;
    let work_parent = root(&args.work)?;
    let output_parent = root(&args.output)?;
    let assets = root(&args.assets)?;
    executable(&args.node)?;
    executable(&args.ffmpeg)?;
    executable(&args.chromium)?;
    let work = root(&work_parent.join(&args.job))?;
    let output = root(&output_parent.join(&args.job))?;
    phase(&work, "roots")?;
    let receipt = validate_runtime(&runtime)?;
    // The owner-installed browser source and actual Host sealed executable
    // must be BYTE-IDENTICAL; a model cannot substitute another program or
    // a same-named system browser through a typed Host tool argument.
    let expected = receipt
        .files
        .get("browser")
        .ok_or_else(|| invalid("Runtime missing pinned browser binary"))?;
    let sealed_browser = fs::metadata(&args.chromium)?;
    if sealed_browser.len() != expected.bytes
        || sealed_browser.len() > 320 * 1024 * 1024
        || file_sha(&args.chromium, 320 * 1024 * 1024)? != expected.sha256
    {
        return Err(invalid(
            "Host sealed browser differs from the exact pinned runtime binary",
        ));
    }
    phase(&work, "runtime")?;
    let plan_bytes = read(&work, "plan.json", 2 * 1024 * 1024)?;
    if sha(&plan_bytes) != args.plan_sha {
        return Err(invalid("Plan digest changed after admission"));
    }
    let plan: HyperframesPlan = serde_json::from_slice(&plan_bytes)?;
    if sha(&read(&runtime, "runtime.json", 1024 * 1024)?) != plan.runtime_receipt_sha256 {
        return Err(invalid(
            "Installed native runtime changed after the owner-selected profile was recorded; explicit update is required",
        ));
    }
    validate_plan(&plan).map_err(|e| invalid(e.to_string()))?;
    let (first_frame, end_frame) = args
        .frame_window
        .unwrap_or((0, plan.document.canvas.frames));
    if end_frame > plan.document.canvas.frames {
        return Err(invalid(
            "Frame window cannot exceed admitted native source timeline",
        ));
    }
    phase(&work, "document")?;
    let html = compile_html(&plan.document).map_err(|e| invalid(e.to_string()))?;
    if sha(html.as_bytes()) != args.source_sha {
        return Err(invalid(
            "Source digest differs from the exact native document",
        ));
    }
    if read(&work, "index.html", 3 * 1024 * 1024)? != html.as_bytes() {
        return Err(invalid(
            "Retained native source was modified before execution",
        ));
    }
    phase(&work, "source")?;
    write_new(&work.join("authoring.js"), AUTHOR.as_bytes())?;
    write_new(&work.join("capture.mjs"), CAPTURE.as_bytes())?;
    write_new(&work.join("sandbox_policy.mjs"), SANDBOX_POLICY.as_bytes())?;
    let mut capture = Command::new(&args.node);
    capture
        .arg(work.join("capture.mjs"))
        .arg(&runtime)
        .arg(&work)
        .arg(&output)
        .arg(&assets)
        .arg(&args.plan_sha)
        .arg(&args.chromium);
    if args.frame_window.is_some() {
        capture
            .arg(first_frame.to_string())
            .arg(end_frame.to_string());
    }
    child_environment(&mut capture, &runtime, &work)?;
    phase(&work, "launcher")?;
    let status = capture.status()?;
    if !status.success() {
        phase(&work, "capture_nonzero")?;
        return Err(invalid(format!(
            "HyperFrames native capture failed with {status}"
        )));
    }
    phase(&work, "capture")?;
    let manifest_bytes = read(&output, "frames.json", 4 * 1024 * 1024)?;
    let manifest: serde_json::Value = serde_json::from_slice(&manifest_bytes)?;
    let c = &plan.document.canvas;
    if !matches!(
        manifest["sandbox_mode"].as_str(),
        Some("chromium-userns" | "semwright-bwrap-outer")
    ) {
        return Err(invalid(
            "Native capture did not attest to its exact confinement profile",
        ));
    }
    let window = args.frame_window.is_some();
    let captured_count = end_frame - first_frame;
    let schema = if window {
        "motionwright.hyperframes-native-frame-window/1"
    } else {
        "motionwright.hyperframes-native-frames/1"
    };
    if manifest["schema"] != schema
        || manifest["source_sha256"] != args.source_sha
        || manifest["plan_sha256"] != args.plan_sha
        || manifest["frame_count"] != captured_count
        || manifest["width"] != c.width
        || manifest["height"] != c.height
    {
        return Err(invalid(
            "Capture receipt differs from the admitted document or window",
        ));
    }
    if window {
        if manifest["timeline_total_frames"] != c.frames
            || manifest["selected_window"] != json!({"start":first_frame,"end_exclusive":end_frame})
            || manifest["observation"]["coverage"] != "selected_window"
            || manifest["observation"]["frames"] != captured_count
        {
            return Err(invalid(
                "Partial renderer attempted to claim a complete native observation",
            ));
        }
    } else if manifest["observation"]["coverage"] != "all_frames"
        || manifest["observation"]["frames"] != c.frames
    {
        return Err(invalid("Full native frame evidence is incomplete"));
    }
    let frames = manifest["frames"]
        .as_array()
        .ok_or_else(|| invalid("Capture omitted frames"))?;
    if frames.len() != captured_count as usize {
        return Err(invalid("Capture frame selection is incomplete"));
    }
    for (offset, frame) in frames.iter().enumerate() {
        let index = usize::try_from(first_frame)
            .map_err(|_| invalid("Native frame index outside bounded platform"))?
            + offset;
        let name = format!("frames/frame-{index:06}.png");
        if frame["frame"] != index || frame["relative_path"] != name {
            return Err(invalid("Capture frame identity/order mismatch"));
        }
        let (path, bytes) = regular(&output, &name, 32 * 1024 * 1024)?;
        if frame["sha256"] != file_sha(&path, 32 * 1024 * 1024)? || frame["bytes"] != bytes {
            return Err(invalid("Capture frame bytes changed"));
        }
    }
    if window {
        // Frame-window mode produces only current-source PNGs and readback for
        // precisely [first,end). It NEVER emits a complete video, cache-hit
        // claim, reused observations, an approved source or an encoder result.
        write_new(&output.join("source.html"), html.as_bytes())?;
        write_new(&output.join("source.json"), &plan_bytes)?;
        let result = json!({
            "schema":"motionwright.hyperframes-native-frame-window-result/1",
            "project_id":plan.project_id,"generation":plan.generation,
            "revision":plan.revision,"scene_id":plan.scene_id,
            "source_sha256":args.source_sha,"plan_sha256":args.plan_sha,
            "frame_count":captured_count,"timeline_total_frames":c.frames,
            "selected_window":{"start":first_frame,"end_exclusive":end_frame},
            "rate":c.rate,"width":c.width,"height":c.height,
            "alpha":c.background.is_none(),"color":"srgb",
            "frames":artifact(&output,"frames.json","application/json",4*1024*1024)?,
            "mezzanine":serde_json::Value::Null,
            "source":artifact(&output,"source.html","text/html",3*1024*1024)?,
            "document":artifact(&output,"source.json","application/json",2*1024*1024)?,
            "observations":artifact(&output,"observations.ndjson","application/x-ndjson",64*1024*1024)?,
            "runtime_receipt_sha256":sha(&read(&runtime,"runtime.json",1024*1024)?),
            "sandbox_mode":manifest["sandbox_mode"],
            "native_frames_rendered":captured_count,
            "native_frames_reused":0,
            "complete_master":false,
            "owner_cache_authority":"NOT_GRANTED",
            "creative_approval":"required"
        });
        write_new(
            &output.join("result.json"),
            &serde_json::to_vec_pretty(&result)?,
        )?;
        phase(&work, "finished")?;
        println!(
            "{}",
            json!({
                "job":args.job,"selected_window":{"start":first_frame,"end_exclusive":end_frame},
                "native_frames_rendered":captured_count,"source_sha256":args.source_sha
            })
        );
        return Ok(());
    }
    let mut encode = Command::new(&args.ffmpeg);
    encode
        .env_clear()
        .env("LANG", "C.UTF-8")
        .env("LC_ALL", "C.UTF-8")
        .current_dir(&output)
        .stdin(Stdio::null())
        .args([
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-n",
            "-threads",
            "1",
            "-framerate",
        ])
        .arg(format!("{}/{}", c.rate.num, c.rate.den))
        .args([
            "-i",
            "frames/frame-%06d.png",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "bgra",
            "-frames:v",
        ])
        .arg(c.frames.to_string())
        .args(["-f", "matroska", "mezzanine.mkv"]);
    let status = encode.status()?;
    if !status.success() {
        phase(&work, "encoder_nonzero")?;
        return Err(invalid(format!(
            "Native mezzanine encoding failed with {status}"
        )));
    }
    phase(&work, "encoder")?;
    // Keep the HTML, original typed plan and source digests with the native result.
    write_new(&output.join("source.html"), html.as_bytes())?;
    write_new(&output.join("source.json"), &plan_bytes)?;
    let result = json!({"schema":"motionwright.hyperframes-runtime-result/1","project_id":plan.project_id,"generation":plan.generation,"revision":plan.revision,"scene_id":plan.scene_id,"source_sha256":args.source_sha,"plan_sha256":args.plan_sha,
        "frame_count":c.frames,"rate":c.rate,"width":c.width,"height":c.height,"alpha":c.background.is_none(),"color":"srgb",
        "frames":artifact(&output,"frames.json","application/json",4*1024*1024)?,
        "mezzanine":artifact(&output,"mezzanine.mkv","video/x-matroska",MAX_OUTPUT)?,
        "source":artifact(&output,"source.html","text/html",3*1024*1024)?,
        "document":artifact(&output,"source.json","application/json",2*1024*1024)?,
        "observations":artifact(&output,"observations.ndjson","application/x-ndjson",64*1024*1024)?,
        "runtime_receipt_sha256":sha(&read(&runtime,"runtime.json",1024*1024)?),"sandbox_mode":manifest["sandbox_mode"],"creative_approval":"required"});
    write_new(
        &output.join("result.json"),
        &serde_json::to_vec_pretty(&result)?,
    )?;
    phase(&work, "finished")?;
    println!(
        "{}",
        json!({"job":args.job,"frame_count":c.frames,"source_sha256":args.source_sha})
    );
    Ok(())
}
fn main() {
    let raw = std::env::args().skip(1).collect::<Vec<_>>();
    if let Err(error) = parse(&raw).and_then(render) {
        eprintln!("{error}");
        std::process::exit(2);
    }
}

fn verify_runtime_inventory(runtime: &Path, receipt: &RuntimeReceipt) -> Result<()> {
    use std::collections::BTreeSet;
    let descriptor = &receipt.inventory;
    if descriptor.path != "runtime-files.json"
        || !hex_digest(&descriptor.sha256)
        || descriptor.files == 0
        || descriptor.files > 20000
        || descriptor.bytes > 2 * 1024 * 1024 * 1024
    {
        return Err(invalid(
            "Installed runtime inventory metadata is outside its explicit contract",
        ));
    }
    let bytes = read(runtime, &descriptor.path, 8 * 1024 * 1024)?;
    if sha(&bytes) != descriptor.sha256 {
        return Err(invalid(
            "Installed runtime inventory changed after owner admission",
        ));
    }
    let inventory: RuntimeInventory = serde_json::from_slice(&bytes)?;
    if inventory.schema != 1 || inventory.files.len() != descriptor.files {
        return Err(invalid("Installed runtime inventory is incomplete"));
    }
    let browser = receipt
        .files
        .get("browser")
        .ok_or_else(|| invalid("Browser runtime identity is missing"))?;
    let browser_directory = Path::new(&browser.path)
        .parent()
        .ok_or_else(|| invalid("Browser directory is absent"))?;
    if !browser_directory.starts_with(".browsers") {
        return Err(invalid(
            "Browser dependency inventory must stay within the owner-installed browser root",
        ));
    }
    let mut expected = BTreeSet::new();
    let mut total = 0u64;
    for entry in &inventory.files {
        let relative = Path::new(&entry.path);
        if !(relative.starts_with("node_modules") || relative.starts_with(browser_directory))
            || !expected.insert(entry.path.clone())
            || !hex_digest(&entry.sha256)
        {
            return Err(invalid(
                "Runtime inventory contains a duplicate, escaping or malformed dependency",
            ));
        }
        let (file, size) = regular(runtime, &entry.path, 512 * 1024 * 1024)?;
        if size != entry.bytes || file_sha(&file, 512 * 1024 * 1024)? != entry.sha256 {
            return Err(invalid(format!(
                "Installed runtime dependency changed: {}",
                entry.path
            )));
        }
        total = total
            .checked_add(size)
            .ok_or_else(|| invalid("Runtime dependency byte count overflow"))?;
        if total > 2 * 1024 * 1024 * 1024 {
            return Err(invalid(
                "Runtime dependency tree exceeds the selected profile",
            ));
        }
    }
    if total != descriptor.bytes {
        return Err(invalid(
            "Runtime inventory total differs from the owner-selected identity",
        ));
    }
    fn walk(
        runtime: &Path,
        directory: &Path,
        actual: &mut BTreeSet<String>,
        depth: u32,
    ) -> Result<()> {
        if depth > 64 {
            return Err(invalid(
                "Runtime dependency hierarchy exceeds its traversal budget",
            ));
        }
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            if entry.file_name() == ".bin" {
                continue;
            }
            let kind = entry.file_type()?;
            if kind.is_symlink() {
                return Err(invalid(
                    "Runtime dependencies cannot traverse symbolic links",
                ));
            }
            if kind.is_dir() {
                walk(runtime, &entry.path(), actual, depth + 1)?;
            } else if kind.is_file() {
                let relative = entry
                    .path()
                    .strip_prefix(runtime)
                    .map_err(|_| invalid("Runtime file escaped its root"))?
                    .to_string_lossy()
                    .replace('\\', "/");
                actual.insert(relative);
            } else {
                return Err(invalid(
                    "Runtime dependency tree contains a non-regular entry",
                ));
            }
            if actual.len() > 20000 {
                return Err(invalid(
                    "Runtime dependency file count exceeds its fixed bound",
                ));
            }
        }
        Ok(())
    }
    let mut actual = BTreeSet::new();
    walk(runtime, &runtime.join("node_modules"), &mut actual, 0)?;
    walk(runtime, &runtime.join(browser_directory), &mut actual, 0)?;
    if actual != expected {
        return Err(invalid(
            "Runtime module tree has additional or missing files; implicit updates are not admitted",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn runner_rejects_unbounded_and_arbitrary_commands() {
        for raw in [
            vec![],
            vec!["render".into()],
            vec!["execute".into(), "untrusted-program".into()],
        ] {
            assert!(parse(&raw).is_err());
        }
    }
    #[test]
    fn source_contains_no_model_selected_program() {
        assert!(CAPTURE.contains("window.__player.renderSeek"));
        assert!(AUTHOR.contains("textContent=run.text"));
        assert!(!AUTHOR.contains("innerHTML"));
    }
}

#[cfg(test)]
mod bounded_window_tests {
    use super::*;
    fn base(mode: &str) -> Vec<String> {
        let mut options = vec!["render".to_owned()];
        for (name, value) in [
            ("--runtime-root", "/runtime"),
            ("--node-sealed", "/node"),
            ("--ffmpeg-sealed", "/ffmpeg"),
            ("--chromium-sealed", "/chromium"),
            ("--work-root", "/work"),
            ("--output-root", "/output"),
            ("--assets-root", "/assets"),
            ("--job", "hf-0123456789abcdef0123456789abcdef"),
            (
                "--plan-sha256",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
            (
                "--source-sha256",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ),
        ] {
            options.push(name.into());
            options.push(value.into());
        }
        if mode == "render-window" {
            options[0] = "render-window".into();
            options.push("--first-frame".into());
            options.push("30".into());
            options.push("--end-frame-exclusive".into());
            options.push("90".into());
        }
        options
    }
    #[test]
    fn full_and_partial_invocations_are_different_exact_commands() {
        assert_eq!(parse(&base("render")).unwrap().frame_window, None);
        assert_eq!(
            parse(&base("render-window")).unwrap().frame_window,
            Some((30, 90))
        );
        let mut malformed = base("render-window");
        malformed[0] = "render".into();
        assert!(parse(&malformed).is_err());
        let mut malformed = base("render");
        malformed[0] = "render-window".into();
        assert!(parse(&malformed).is_err());
    }
    #[test]
    fn invalid_or_noncanonical_window_boundaries_are_rejected() {
        for (first, end) in [
            ("90", "90"),
            ("90", "30"),
            ("0", "3601"),
            ("00", "90"),
            ("-1", "90"),
            ("0", "0"),
            ("NaN", "90"),
            ("30", "090"),
        ] {
            let mut raw = base("render-window");
            raw[22] = first.into();
            raw[24] = end.into();
            assert!(
                parse(&raw).is_err(),
                "Unexpected accepted window {first}..{end}"
            );
        }
    }
    #[test]
    fn no_unknown_flags_or_duplicate_authority_are_admitted() {
        let mut raw = base("render-window");
        raw[21] = "--script".into();
        assert!(parse(&raw).is_err());
        let mut raw = base("render-window");
        raw[23] = "--first-frame".into();
        assert!(parse(&raw).is_err());
    }
}
