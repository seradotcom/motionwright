//! Private input preparation against the exact reviewed SDK, never a Native run.
//!
//! This source is not installed in the SDK. Its owner must separately retain the
//! exact build/source/lock/binary evidence before using the generated input.
//! Usage: prepare-fixedtxt-managed-project ABSOLUTE_TEMPLATE FRESH_ABSOLUTE_OUTPUT
use semwright_driver_motion_canvas::authoring;
use semwright_motion_authoring::Film;
use semwright_semantic_composition::strict_decode;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::ExitCode;

type Result<T> = std::result::Result<T, String>;
const MAX_BYTES: u64 = 4 * 1024 * 1024;
const TEMPLATE_SHA256: &str =
    "5b1f9e7befb11c3ef6354a9b3b292d105647f444cac4a13d6eb8d27bf7c10b97";
const EXPECTED_SDK: &str = "5c2ac3107afbb083afb00d67a28edd0f104f8e15";

fn need(ok: bool, message: &str) -> Result<()> {
    if ok { Ok(()) } else { Err(message.into()) }
}

fn sha(raw: &[u8]) -> String {
    hex::encode(Sha256::digest(raw))
}

fn plain_path(path: &Path, directory: bool) -> Result<()> {
    need(path.is_absolute(), "Preparation paths must be absolute")?;
    need(
        !path.components().any(|part| matches!(part, Component::ParentDir | Component::CurDir)),
        "Preparation paths must not contain dot components",
    )?;
    for current in path.ancestors() {
        let metadata = fs::symlink_metadata(current).map_err(|error| error.to_string())?;
        need(!metadata.file_type().is_symlink(), "Preparation path contains a symbolic link")?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            need(metadata.file_attributes() & 0x400 == 0,
                 "Preparation path contains a Windows reparse point")?;
        }
        need(
            if current == path && !directory { metadata.is_file() } else { metadata.is_dir() },
            "Preparation input/output ancestor has the wrong file type",
        )?;
    }
    Ok(())
}

fn original(path: &Path) -> Result<Vec<u8>> {
    plain_path(path, false)?;
    let before = fs::metadata(path).map_err(|error| error.to_string())?;
    need(before.len() > 0 && before.len() <= MAX_BYTES, "Input outside the 4 MiB bound")?;
    let mut stream = File::open(path).map_err(|error| error.to_string())?;
    let mut raw = Vec::new();
    (&mut stream).take(MAX_BYTES + 1).read_to_end(&mut raw).map_err(|error| error.to_string())?;
    let opened = stream.metadata().map_err(|error| error.to_string())?;
    plain_path(path, false)?;
    let after = fs::metadata(path).map_err(|error| error.to_string())?;
    need(
        raw.len() as u64 == before.len() && opened.len() == before.len()
            && after.len() == before.len() && opened.modified().ok() == before.modified().ok()
            && after.modified().ok() == before.modified().ok(),
        "Input changed during bounded read",
    )?;
    Ok(raw)
}

fn new_original(root: &Path, name: &str, raw: &[u8]) -> Result<serde_json::Value> {
    need(!raw.is_empty() && raw.len() as u64 <= MAX_BYTES, "Generated input outside 4 MiB bound")?;
    plain_path(root, true)?;
    let path = root.join(name);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut stream = options.open(&path).map_err(|error| error.to_string())?;
    stream.write_all(raw).map_err(|error| error.to_string())?;
    stream.sync_all().map_err(|error| error.to_string())?;
    drop(stream);
    need(original(&path)?.as_slice() == raw, "New original failed its physical readback")?;
    Ok(json!({"member": name, "bytes": raw.len(), "sha256": sha(raw)}))
}

fn run() -> Result<()> {
    let args: Vec<PathBuf> = std::env::args_os().skip(1).map(PathBuf::from).collect();
    need(args.len() == 2, "Usage: ABSOLUTE_TEMPLATE FRESH_ABSOLUTE_OUTPUT")?;
    let template = &args[0];
    let output = &args[1];
    need(output.is_absolute() && output.file_name().is_some(), "Output must be a fresh absolute directory")?;
    need(!output.components().any(|part| matches!(part, Component::ParentDir | Component::CurDir)),
         "Output must not contain dot components")?;
    let parent = output.parent().ok_or("Output parent missing")?;
    plain_path(parent, true)?;
    match fs::symlink_metadata(output) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
        _ => return Err("Output already exists or cannot be inspected; nothing will be overwritten".into()),
    }
    let template_bytes = original(template)?;
    need(sha(&template_bytes) == TEMPLATE_SHA256, "Template differs from the preserved SDK original")?;
    let film: Film = strict_decode(&template_bytes).map_err(|error| format!("Invalid Film: {error:?}"))?;
    need(film.assets.is_empty(), "This preparation cannot invent or import managed assets")?;

    // Exactly one real factory call: it generates the Project identity/binding.
    // Preserve this result once and use identical bytes across both arms.
    let (project, realization) = authoring::project(&film, None)
        .map_err(|error| format!("Authoring factory failed: {error:?}"))?;
    authoring::check_binding(&project)
        .map_err(|error| format!("Generated binding failed: {error:?}"))?;
    need(project.id == film.id && project.revision == 1 && !project.generation.is_empty()
         && project.assets.is_empty() && project.authoring.is_some(),
         "Generated Project does not preserve this template/initial managed state")?;
    let frame_count = authoring::frame_count(&project).map_err(|error| format!("Frame count failed: {error:?}"))?;
    let project_bytes = serde_json::to_vec(&project).map_err(|error| error.to_string())?;
    let realization_bytes = serde_json::to_vec(&realization).map_err(|error| error.to_string())?;
    need(project_bytes.len() as u64 <= MAX_BYTES && realization_bytes.len() as u64 <= MAX_BYTES,
         "Generated Project or realization exceeds the existing component bound")?;
    need(original(template)? == template_bytes, "Template changed before publication")?;

    // Parent must already be owner-private. Preserve all partial output on error;
    // no deletion, reuse, retry, chmod of existing objects or SDK edit occurs.
    let mut directory = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        directory.mode(0o700);
    }
    directory.create(output).map_err(|error| error.to_string())?;
    let files = vec![
        new_original(output, "template_film.original.json", &template_bytes)?,
        new_original(output, "managed_project.original.json", &project_bytes)?,
        new_original(output, "realization.original.json", &realization_bytes)?,
    ];
    need(original(template)? == template_bytes, "Template changed during publication")?;
    let receipt = json!({
        "kind": "PRIVATE_FIXEDTXT_SOURCE_INPUT_PREPARATION",
        "status": "GENERATED_BY_REAL_AUTHORING_API_NOT_NATIVE_EVIDENCE",
        "expected_sdk_revision": EXPECTED_SDK,
        "linked_sdk_revision_verified_by_this_program": false,
        "owner_exact_build_source_lock_and_binary_receipt_required": true,
        "api": "semwright_driver_motion_canvas::authoring::project(&film,None)",
        "factory_calls": 1,
        "binding_revalidated": true,
        "project_id": project.id,
        "generation": project.generation,
        "revision": project.revision,
        "template_frame_count": frame_count,
        "fixture_builder_executed": false,
        "project_files": [],
        "originals": files,
        "historical_Project97ebb_recovered_or_reconstructed": false,
        "parent_private_acl_required": true,
        "native_calls": 0, "renders": 0,
        "automatic_retry": false,
        "protocol_approved": false, "r28_issued": false,
        "formal_acceptance": false, "release_authorized": false
    });
    let receipt_bytes = serde_json::to_vec_pretty(&receipt).map_err(|error| error.to_string())?;
    new_original(output, "PREPARATION_RECEIPT.json", &receipt_bytes)?;
    println!("SOURCE_INPUT_PREPARED; NO_NATIVE_RESULT; reuse the frozen Project bytes in both arms");
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("FixedTxt source input preparation failed: {error}");
            ExitCode::FAILURE
        }
    }
}
