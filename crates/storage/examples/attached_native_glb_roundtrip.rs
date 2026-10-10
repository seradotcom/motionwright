//! Portable replay of one real Blender-exported GLB using canonical Store.
//! Disposable synthetic owner source only; no plugins or approvals are granted.
use motionwright_domain::{Asset, Change, Fidelity, FidelityReport, NativeCapsule, Project};
use motionwright_storage::Store;
use sha2::{Digest, Sha256};
use std::{env, fs, io::Write, path::PathBuf};
use uuid::Uuid;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 3 {
        return Err("Expected GLB, new fixture root, new receipt".into());
    }
    let source = PathBuf::from(&args[0]);
    let sandbox = PathBuf::from(&args[1]);
    let receipt = PathBuf::from(&args[2]);
    let original = fs::symlink_metadata(&source)?;
    if !original.is_file()
        || original.file_type().is_symlink()
        || original.len() < 20
        || original.len() > 32 * 1024 * 1024
    {
        return Err("Original source must be a bounded regular file".into());
    }
    let original_bytes = fs::read(&source)?;
    if !original_bytes.starts_with(b"glTF") {
        return Err("Source must be original GLB".into());
    }
    if sandbox.exists() || receipt.exists() {
        return Err("Never overwrite existing evidence".into());
    }
    fs::create_dir(&sandbox)?;
    let mut source_store = Store::open(sandbox.join("original.sqlite3"))?;
    let original_blob = source_store.ingest_blob_file(&source)?;
    let mut project = Project::new("Owner original Blender GLB")?;
    project.apply_change(&Change::AddScene {
        name: "Original Blender stage".into(),
        objective: "Preserve source geometry and original metadata".into(),
        duration_seconds: 3,
    })?;
    let asset_id = Uuid::from_u128(10);
    project.assets.push(Asset {
        id: asset_id,
        name: "original-blender-export.glb".into(),
        media_type: "model/gltf-binary".into(),
        content_sha256: Some(original_blob.sha256.clone()),
        source_revision: Some("disposable-original-native-ci".into()),
    });
    let capsule = NativeCapsule {
        id: Uuid::from_u128(11),
        scene_id: project.scenes[0].id,
        source_asset_id: asset_id,
        source_sha256: original_blob.sha256.clone(),
        label: "Owner original native GLB".into(),
        fidelity: FidelityReport {
            renderer: "blender".into(),
            renderer_version: "4.x".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Unavailable,
            editable: Fidelity::Unavailable,
            losses: vec!["No external semantic editing/renderer equivalence admitted".into()],
            evidence_sha256: None,
        },
        editable_parameters: Vec::new(),
        native_editor_hint: "Open in owner-controlled Blender".into(),
    };
    project.production_design.capsules.push(capsule.clone());
    project.validate()?;
    source_store.create_project(&project)?;
    if source_store.read_blob(&original_blob.sha256, 32 * 1024 * 1024)? != original_bytes {
        return Err("Original CAS blob changed".into());
    }
    let portable = sandbox.join("original-project.motionwright");
    let manifest = source_store.export_project_bundle(project.id, &portable)?;
    if manifest.blobs.len() != 1 || manifest.blobs[0].sha256 != original_blob.sha256 {
        return Err("Portable original source was not retained".into());
    }
    let moved = portable.join(&manifest.blobs[0].relative_path);
    if fs::read(&moved)? != original_bytes {
        return Err("Portable GLB bytes changed".into());
    }
    let mut second = Store::open(sandbox.join("reopened.sqlite3"))?;
    let inspected = second.inspect_project_bundle(&portable)?;
    if inspected.blob_count != 1 || inspected.total_blob_bytes != original_blob.size_bytes {
        return Err("Native portable inspection lost source metadata".into());
    }
    let reopened = second.import_project_bundle(&portable)?;
    if reopened.id != project.id
        || reopened.generation == project.generation
        || reopened.production_design.capsules != vec![capsule]
        || reopened.assets != project.assets
        || reopened
            .extensions
            .iter()
            .any(|extension| extension.enabled)
    {
        return Err("Portable restoration changed capsule, generation or authority".into());
    }
    let readback = second.read_blob(&original_blob.sha256, 32 * 1024 * 1024)?;
    if readback != original_bytes || hex::encode(Sha256::digest(&readback)) != original_blob.sha256
    {
        return Err("Restored exact GLB source bytes changed".into());
    }
    let result = serde_json::json!({
        "schema":"motionwright.real-glb-portable-source/1",
        "source_sha256":original_blob.sha256,
        "glb_bytes":original_blob.size_bytes,
        "portable_format_version":manifest.format_version,
        "generation_rotated":true,
        "source_blender_glb_exact_bytes":"PASS",
        "portable_original_native_capsule":"PASS",
        "external_renderer_permissions":"NONE",
        "external_semantic_editability":"NOT_ADMITTED",
        "creative_owner_approval":"NOT_PERFORMED",
        "publication_approved":false
    });
    let mut sink = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(receipt)?;
    serde_json::to_writer_pretty(&mut sink, &result)?;
    sink.write_all(b"\n")?;
    println!("{}", result);
    Ok(())
}
