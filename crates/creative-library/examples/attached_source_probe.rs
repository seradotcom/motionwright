//! Disposable CI-only byte inspection of a provided native GLB.
//! Never executes or rewrites the source, installs a plugin or approves it.
use motionwright_creative_library::{
    MAX_ATTACHED_INSPECTION_BYTES, inspect_attached_native_source,
};
use motionwright_domain::{Asset, Fidelity, FidelityReport, NativeCapsule};
use sha2::{Digest, Sha256};
use std::{
    env, fs,
    io::{self, Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

fn read_observed_size(reader: &mut impl Read, initial_size: u64) -> io::Result<Vec<u8>> {
    if !(20..=MAX_ATTACHED_INSPECTION_BYTES as u64).contains(&initial_size) {
        return Err(io::Error::other(
            "Native source exceeds the GLB inspection budget",
        ));
    }
    // One additional byte is enough to reject growth without following a
    // growing reader to EOF. This bounds bytes, not wall time or all file races.
    let mut bytes = Vec::new();
    reader.take(initial_size + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 != initial_size {
        return Err(io::Error::other(
            "Native source changed its observed length",
        ));
    }
    Ok(bytes)
}

fn read_original_glb(source: &Path) -> io::Result<Vec<u8>> {
    let before = fs::symlink_metadata(source)?;
    if !before.is_file() || before.file_type().is_symlink() {
        return Err(io::Error::other(
            "Native source must be a regular non-symlink file",
        ));
    }
    let modified = before.modified()?;
    let mut file = fs::File::open(source)?;
    let opened = file.metadata()?;
    if !opened.is_file() || opened.len() != before.len() || opened.modified()? != modified {
        return Err(io::Error::other("Native source changed before inspection"));
    }
    let bytes = read_observed_size(&mut file, before.len())?;
    let after = file.metadata()?;
    let path_after = fs::symlink_metadata(source)?;
    if !path_after.is_file()
        || path_after.file_type().is_symlink()
        || after.len() != before.len()
        || path_after.len() != before.len()
        || after.modified()? != modified
        || path_after.modified()? != modified
    {
        return Err(io::Error::other("Native source changed during inspection"));
    }
    Ok(bytes)
}

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
    let bytes = read_original_glb(&source)?;
    let sha = hex::encode(Sha256::digest(&bytes));
    let asset = Asset {
        id: Uuid::from_u128(16),
        name: "Provided native GLB CI source".into(),
        media_type: "model/gltf-binary".into(),
        content_sha256: Some(sha.clone()),
        source_revision: Some("ci-native-source-inspection".into()),
    };
    let capsule = NativeCapsule {
        id: Uuid::from_u128(17),
        scene_id: Uuid::from_u128(18),
        source_asset_id: asset.id,
        source_sha256: sha.clone(),
        label: asset.name.clone(),
        fidelity: FidelityReport {
            renderer: "unobserved-native-source".into(),
            renderer_version: "not-observed-by-this-inspector".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Unavailable,
            editable: Fidelity::Unavailable,
            losses: vec![
                "GLB is structurally observable but imported semantic editing is not established"
                    .into(),
            ],
            evidence_sha256: None,
        },
        editable_parameters: vec![],
        native_editor_hint: "Owner-controlled source application".into(),
        property_fidelity: vec![],
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_source_is_read_exactly() {
        let source = b"twenty original bytes";
        assert_eq!(
            read_observed_size(&mut source.as_slice(), source.len() as u64).unwrap(),
            source
        );
    }

    #[test]
    fn truncated_source_is_rejected() {
        let source = b"twenty original bytes";
        assert!(read_observed_size(&mut source.as_slice(), source.len() as u64 + 1).is_err());
    }

    #[test]
    fn growing_source_is_rejected_after_at_most_initial_size_plus_one_bytes() {
        struct GrowingReader {
            consumed: usize,
            calls: usize,
        }
        impl Read for GrowingReader {
            fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
                self.calls += 1;
                // An independent finite guard fails an unbounded read loop.
                assert!(self.calls <= 8, "reader did not stop at its byte budget");
                out.fill(b'x');
                self.consumed += out.len();
                assert!(self.consumed <= 21, "reader exceeded initial_size + 1");
                Ok(out.len())
            }
        }
        let mut reader = GrowingReader {
            consumed: 0,
            calls: 0,
        };
        assert!(read_observed_size(&mut reader, 20).is_err());
        assert_eq!(reader.consumed, 21);
    }
}
