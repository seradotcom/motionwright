//! Export original compiler bytes from a saved camera source before native dispatch.
//!
//! This fixture applies no Change, creates no Host grant, and starts no renderer.
//! Its output is source evidence, not a native or registered-source reopen result.
use motionwright_domain::RevisionStamp;
use motionwright_native::manim_camera_projection::prepare_saved_manim_camera_program_source_only;
use motionwright_service::StudioService;
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    env,
    error::Error,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};
use uuid::Uuid;

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const MAX_SEED_BYTES: u64 = 64 * 1024;
const MAX_DATABASE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_SOURCE_BYTES: usize = 256 * 1024;
const MAX_PROJECT_BYTES: usize = 4 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SeedReceipt {
    schema: String,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    scene_id: Uuid,
    deliverable_id: Uuid,
    reframe_id: Uuid,
    source_project_sha256: String,
    frame_count: u64,
    frame_rate_num: u64,
    frame_rate_den: u64,
    source_objects: Vec<Uuid>,
    fresh_sqlite_readback: bool,
    native_dispatch_performed: bool,
    decoded_media_verified: bool,
    rights: String,
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn plain_path(path: &Path, leaf_may_be_absent: bool) -> Result<()> {
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, Component::ParentDir | Component::CurDir))
    {
        return Err("Use absolute, nontraversing owned paths".into());
    }
    for (index, ancestor) in path.ancestors().enumerate() {
        let metadata = match fs::symlink_metadata(ancestor) {
            Ok(metadata) => metadata,
            Err(error)
                if index == 0
                    && leaf_may_be_absent
                    && error.kind() == std::io::ErrorKind::NotFound =>
            {
                continue;
            }
            Err(error) => return Err(error.into()),
        };
        if metadata.file_type().is_symlink() || (index != 0 && !metadata.is_dir()) {
            return Err("An input/output ancestor is not an ordinary owned directory".into());
        }
    }
    Ok(())
}

fn ordinary_file(path: &Path, maximum: u64) -> Result<u64> {
    plain_path(path, false)?;
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > maximum {
        return Err("Input is not a nonempty ordinary file within its byte budget".into());
    }
    Ok(metadata.len())
}

fn read_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>> {
    let before = ordinary_file(path, maximum)?;
    let mut bytes = Vec::new();
    File::open(path)?
        .take(maximum + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != before || ordinary_file(path, maximum)? != before {
        return Err("Input changed during its bounded read".into());
    }
    Ok(bytes)
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    plain_path(path, true)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    if read_bounded(path, bytes.len() as u64)? != bytes {
        return Err("New source evidence failed exact byte readback".into());
    }
    Ok(())
}

fn write_json(path: &Path, value: &serde_json::Value) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    write_new(path, &bytes)
}

fn run(database: &Path, seed_path: &Path, output: &Path) -> Result<()> {
    ordinary_file(database, MAX_DATABASE_BYTES)?;
    let original_seed = read_bounded(seed_path, MAX_SEED_BYTES)?;
    let seed: SeedReceipt = serde_json::from_slice(&original_seed)?;
    if seed.schema != "motionwright.native.manim-camera-seed/1"
        || seed.frame_count != 120
        || seed.frame_rate_num != 30
        || seed.frame_rate_den != 1
        || seed.source_objects.len() != 1
        || !seed.fresh_sqlite_readback
        || seed.native_dispatch_performed
        || seed.decoded_media_verified
        || seed.source_project_sha256.len() != 64
        || !seed
            .source_project_sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(
            "The original seed does not identify the closed source-only 120-frame fixture".into(),
        );
    }
    plain_path(output, true)?;
    if output.try_exists()?
        || !output.parent().is_some_and(Path::is_dir)
        || database.starts_with(output)
        || seed_path.starts_with(output)
    {
        return Err("Output must be a new directory separate from the original inputs".into());
    }
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(output)?;
    write_json(
        &output.join("start.json"),
        &json!({
            "schema": "motionwright.manim.camera.plan-export-intent/1",
            "state": "INCOMPLETE_SOURCE_EXPORT",
            "database": database,
            "original_seed_path": seed_path,
            "original_seed_sha256": sha256(&original_seed),
            "project_id": seed.project_id,
            "generation": seed.generation,
            "revision": seed.revision,
            "native_dispatch_performed": false,
            "provider_grant_created": false,
            "formal_acceptance": false
        }),
    )?;
    let service = StudioService::open(database)?;
    let project = service.project(seed.project_id)?;
    let snapshot = serde_json::to_vec(&project)?;
    if project.id != seed.project_id
        || project.generation != seed.generation
        || project.revision != seed.revision
        || snapshot.len() > MAX_PROJECT_BYTES
        || sha256(&snapshot) != seed.source_project_sha256
    {
        return Err(
            "Saved Project differs from the original seed's full identity and bytes".into(),
        );
    }
    let expected = RevisionStamp::from(&project);
    let compiled = prepare_saved_manim_camera_program_source_only(
        &service,
        project.id,
        &expected,
        seed.scene_id,
        seed.deliverable_id,
    )?;
    let plan = &compiled.prepared.source;
    let program = &compiled.trusted_compiler;
    if plan.reframe.id != seed.reframe_id
        || plan
            .nodes
            .iter()
            .map(|node| node.node_id)
            .collect::<Vec<_>>()
            != seed.source_objects
        || plan.clock.frame_count != 120
        || plan.render_profile.width != 640
        || plan.render_profile.height != 360
        || plan.render_profile.frame_rate != 30
        || program.plan_document.is_empty()
        || program.plan_document.len() > MAX_SOURCE_BYTES
        || program.python_source.len() > MAX_SOURCE_BYTES
        || sha256(&program.plan_document) != program.plan_sha256
        || sha256(program.python_source.as_bytes()) != program.source_sha256
        || compiled.native_execution_performed
        || compiled.native_permissions_verified
        || program.native_execution_performed
        || program.runtime_sample_pts_verified
    {
        return Err(
            "Original compiler output is outside the saved, source-only camera fixture".into(),
        );
    }
    if service.project(project.id)? != project
        || read_bounded(seed_path, MAX_SEED_BYTES)? != original_seed
    {
        return Err("Original source changed before evidence export".into());
    }
    write_new(&output.join("seed-receipt.original.json"), &original_seed)?;
    write_new(&output.join("source-project.json"), &snapshot)?;
    // Preserve both outputs directly. Never reproduce Rust formatting in a
    // different serializer or derive expectations from a rendered frame.
    write_new(&output.join("plan.json"), &program.plan_document)?;
    write_new(
        &output.join("compiled-source.py"),
        program.python_source.as_bytes(),
    )?;
    if service.project(project.id)? != project
        || read_bounded(seed_path, MAX_SEED_BYTES)? != original_seed
    {
        return Err("Original source changed during evidence export".into());
    }
    drop(service);
    let evidence = json!({
        "schema": "motionwright.manim.camera.plan-export/1",
        "state": "ORIGINAL_COMPILER_BYTES_EXPORTED_SOURCE_ONLY",
        "database": database,
        "canonical_stamp": expected,
        "binding": plan.binding,
        "source_project_sha256": seed.source_project_sha256,
        "source_project_bytes": snapshot.len(),
        "original_seed_sha256": sha256(&original_seed),
        "original_seed_bytes": original_seed.len(),
        "source_plan_compact_sha256": compiled.prepared.source_plan_sha256,
        "plan": {"path": "plan.json", "bytes": program.plan_document.len(), "sha256": program.plan_sha256},
        "compiled_source": {"path": "compiled-source.py", "bytes": program.python_source.len(), "sha256": program.source_sha256},
        "compiler_id": program.compiler_id,
        "source_project_unchanged": true,
        "original_seed_unchanged": true,
        "canonical_project_observed_in_this_process": true,
        "canonical_change_applied": false,
        "native_dispatch_performed": false,
        "provider_grant_created": false,
        "generated_python_executed": false,
        "registered_native_source_reopen_verified": false,
        "decoded_media_verified": false,
        "four_phase_verification": false,
        "source_rights_declaration": seed.rights,
        "independent_rights_acceptance": false,
        "human_acceptance": false,
        "formal_acceptance": false
    });
    write_json(&output.join("source-export.json"), &evidence)?;
    println!("{}", serde_json::to_string(&evidence)?);
    Ok(())
}

fn main() -> Result<()> {
    let args = env::args_os()
        .skip(1)
        .map(PathBuf::from)
        .collect::<Vec<_>>();
    if args.len() != 3 {
        return Err(
            "usage: ci-manim-camera-plan DATABASE_ABSOLUTE SEED_JSON_ABSOLUTE OUTPUT_NEW_DIRECTORY"
                .into(),
        );
    }
    run(&args[0], &args[1], &args[2])
}
