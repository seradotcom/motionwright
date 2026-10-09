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
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

const AUTHOR: &str = include_str!("../../../../runtime/hyperframes/authoring.js");
const CAPTURE: &str = include_str!("../../../../runtime/hyperframes/capture.mjs");
const MAX_OUTPUT: u64 = 512 * 1024 * 1024;
#[derive(Debug)]
struct Args {
    runtime: PathBuf,
    node: PathBuf,
    ffmpeg: PathBuf,
    work: PathBuf,
    output: PathBuf,
    assets: PathBuf,
    job: String,
    plan_sha: String,
    source_sha: String,
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
#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
struct Artifact {
    relative_path: String,
    sha256: String,
    bytes: u64,
    media_type: &'static str,
}
fn parse(raw: &[String]) -> Result<Args> {
    if raw.first().map(String::as_str) != Some("render") || raw.len() != 19 {
        return Err(invalid(
            "Runner requires the exact bounded render argument set",
        ));
    }
    let mut values = BTreeMap::new();
    for pair in raw[1..].chunks_exact(2) {
        if ![
            "--runtime-root",
            "--node-sealed",
            "--ffmpeg-sealed",
            "--work-root",
            "--output-root",
            "--assets-root",
            "--job",
            "--plan-sha256",
            "--source-sha256",
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
    Ok(Args {
        runtime: PathBuf::from(get("--runtime-root")?),
        node: PathBuf::from(get("--node-sealed")?),
        ffmpeg: PathBuf::from(get("--ffmpeg-sealed")?),
        work: PathBuf::from(get("--work-root")?),
        output: PathBuf::from(get("--output-root")?),
        assets: PathBuf::from(get("--assets-root")?),
        job,
        plan_sha,
        source_sha,
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
        || receipt.capture_profile != "hyperframes-core-firefox-png-v1"
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
        let (path, size) = regular(runtime, &file.path, 300 * 1024 * 1024)?;
        if size != file.bytes || file_sha(&path, 300 * 1024 * 1024)? != file.sha256 {
            return Err(invalid("Pinned runtime dependency changed on disk"));
        }
    }
    if sha(&read(runtime, "package-lock.json", 2 * 1024 * 1024)?) != receipt.npm_lock_sha256 {
        return Err(invalid(
            "Runtime dependency lock changed after installation",
        ));
    }
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
fn render(args: Args) -> Result<()> {
    let runtime = root(&args.runtime)?;
    let work_parent = root(&args.work)?;
    let output_parent = root(&args.output)?;
    let assets = root(&args.assets)?;
    executable(&args.node)?;
    executable(&args.ffmpeg)?;
    let _receipt = validate_runtime(&runtime)?;
    let work = root(&work_parent.join(&args.job))?;
    let output = root(&output_parent.join(&args.job))?;
    let plan_bytes = read(&work, "plan.json", 2 * 1024 * 1024)?;
    if sha(&plan_bytes) != args.plan_sha {
        return Err(invalid("Plan digest changed after admission"));
    }
    let plan: HyperframesPlan = serde_json::from_slice(&plan_bytes)?;
    validate_plan(&plan).map_err(|e| invalid(e.to_string()))?;
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
    write_new(&work.join("authoring.js"), AUTHOR.as_bytes())?;
    write_new(&work.join("capture.mjs"), CAPTURE.as_bytes())?;
    let mut capture = Command::new(&args.node);
    capture
        .arg(work.join("capture.mjs"))
        .arg(&runtime)
        .arg(&work)
        .arg(&output)
        .arg(&assets)
        .arg(&args.plan_sha);
    child_environment(&mut capture, &runtime, &work)?;
    let status = capture.status()?;
    if !status.success() {
        return Err(invalid(format!(
            "HyperFrames native capture failed with {status}"
        )));
    }
    let manifest_bytes = read(&output, "frames.json", 4 * 1024 * 1024)?;
    let manifest: serde_json::Value = serde_json::from_slice(&manifest_bytes)?;
    let c = &plan.document.canvas;
    if manifest["schema"] != "motionwright.hyperframes-native-frames/1"
        || manifest["source_sha256"] != args.source_sha
        || manifest["plan_sha256"] != args.plan_sha
        || manifest["frame_count"] != c.frames
        || manifest["width"] != c.width
        || manifest["height"] != c.height
    {
        return Err(invalid(
            "Capture receipt differs from the admitted document",
        ));
    }
    let frames = manifest["frames"]
        .as_array()
        .ok_or_else(|| invalid("Capture omitted frames"))?;
    if frames.len() != c.frames as usize {
        return Err(invalid("Capture frame set is incomplete"));
    }
    for (index, frame) in frames.iter().enumerate() {
        let name = format!("frames/frame-{index:06}.png");
        if frame["frame"] != index || frame["relative_path"] != name {
            return Err(invalid("Capture frame identity/order mismatch"));
        }
        let (path, bytes) = regular(&output, &name, 32 * 1024 * 1024)?;
        if frame["sha256"] != file_sha(&path, 32 * 1024 * 1024)? || frame["bytes"] != bytes {
            return Err(invalid("Capture frame bytes changed"));
        }
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
        return Err(invalid(format!(
            "Native mezzanine encoding failed with {status}"
        )));
    }
    // Keep the HTML, original typed plan and source digests with the native result.
    write_new(&output.join("source.html"), html.as_bytes())?;
    write_new(&output.join("source.json"), &plan_bytes)?;
    let result = json!({"schema":"motionwright.hyperframes-runtime-result/1","source_sha256":args.source_sha,"plan_sha256":args.plan_sha,
        "frame_count":c.frames,"rate":c.rate,"width":c.width,"height":c.height,"alpha":c.background.is_none(),"color":"srgb",
        "frames":artifact(&output,"frames.json","application/json",4*1024*1024)?,
        "mezzanine":artifact(&output,"mezzanine.mkv","video/x-matroska",MAX_OUTPUT)?,
        "source":artifact(&output,"source.html","text/html",3*1024*1024)?,
        "document":artifact(&output,"source.json","application/json",2*1024*1024)?,
        "observations":artifact(&output,"observations.ndjson","application/x-ndjson",64*1024*1024)?,
        "runtime_receipt_sha256":sha(&read(&runtime,"runtime.json",1024*1024)?),"creative_approval":"required"});
    write_new(
        &output.join("result.json"),
        &serde_json::to_vec_pretty(&result)?,
    )?;
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
