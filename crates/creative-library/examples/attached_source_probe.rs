//! Disposable CI-only byte inspection of a Blender-exported actual GLB.
//! Never executes or rewrites the source, installs a plugin or approves it.
use motionwright_creative_library::inspect_attached_native_source;
use motionwright_domain::{Asset, Fidelity, FidelityReport, NativeCapsule};
use sha2::{Digest, Sha256};
use std::{env, fs, path::PathBuf};
use uuid::Uuid;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 2 {
        return Err("Expected exact input.glb and new read-only receipt.json".into());
    }
    let source = PathBuf::from(&args[0]);
    let output = PathBuf::from(&args[1]);
    if output.exists() {
        return Err("Inspection never replaces a previous creative receipt".into());
    }
    let meta = fs::symlink_metadata(&source)?;
    if !meta.is_file()
        || meta.file_type().is_symlink()
        || meta.len() > 32 * 1024 * 1024
        || meta.len() < 20
    {
        return Err("Native source GLB must be a real bounded regular file".into());
    }
    let bytes = fs::read(&source)?;
    let sha = hex::encode(Sha256::digest(&bytes));
    let asset = Asset {
        id: Uuid::from_u128(16),
        name: "Original Blender-exported GLB CI fixture".into(),
        media_type: "model/gltf-binary".into(),
        content_sha256: Some(sha.clone()),
        source_revision: Some("blender-ci-native-source".into()),
    };
    let capsule = NativeCapsule {
        id: Uuid::from_u128(17),
        scene_id: Uuid::from_u128(18),
        source_asset_id: asset.id,
        source_sha256: sha.clone(),
        label: asset.name.clone(),
        fidelity: FidelityReport {
            renderer: "blender".into(),
            renderer_version: "original-disposable-native-stage".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Unavailable,
            losses: vec![
                "GLB is structurally observable but imported semantic editing is not established"
                    .into(),
            ],
            evidence_sha256: None,
        },
        editable_parameters: vec![],
        native_editor_hint: "Owner-controlled Blender".into(),
    };
    let report = inspect_attached_native_source(&capsule, &asset, &bytes)?;
    if report.source_format != "glb_2_0_structured_attach"
        || report.glb_chunks.is_empty()
        || report.properties.is_empty()
        || report.semantically_editable_by_motionwright
        || report.runtime_or_imported_code_executed
    {
        return Err("Original GLB structure was not safely observed".into());
    }
    let parent = output.parent().ok_or("Receipt output has no parent")?;
    if !parent.is_dir() {
        return Err("Native receipt output parent is unavailable".into());
    }
    let serialized = serde_json::to_vec_pretty(&report)?;
    let mut receipt = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output)?;
    use std::io::Write;
    receipt.write_all(&serialized)?;
    receipt.write_all(b"\n")?;
    println!(
        "{}",
        serde_json::json!({
            "source_sha256":sha,
            "original_glb_structure":"OBSERVED_NOT_IMPORTED",
            "glb_chunks":report.glb_chunks.len(),
            "typed_structures":report.properties.iter()
                .filter(|p|p.disposition==motionwright_creative_library::AttachedDisposition::StructuredObservable)
                .count(),
            "human_approval":"NOT_PERFORMED",
        })
    );
    Ok(())
}
