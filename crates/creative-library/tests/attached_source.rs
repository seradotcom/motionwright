use motionwright_creative_library::{
    AttachedDisposition, AttachedSourceInspection, MAX_ATTACHED_INSPECTION_BYTES,
    inspect_attached_native_source,
};
use motionwright_domain::{Asset, Fidelity, FidelityReport, NativeCapsule};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn original_glb(document: Value, chunks: &[(u32, Vec<u8>)]) -> Vec<u8> {
    let mut json = serde_json::to_vec(&document).unwrap();
    while !json.len().is_multiple_of(4) {
        json.push(b' ');
    }
    let mut bytes = vec![0; 12];
    bytes.extend_from_slice(&(json.len() as u32).to_le_bytes());
    bytes.extend_from_slice(b"JSON");
    bytes.extend_from_slice(&json);
    for (kind, data) in chunks {
        assert_eq!(data.len() % 4, 0);
        bytes.extend_from_slice(&(data.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&kind.to_le_bytes());
        bytes.extend_from_slice(data);
    }
    bytes[..4].copy_from_slice(b"glTF");
    bytes[4..8].copy_from_slice(&2_u32.to_le_bytes());
    let size = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&size.to_le_bytes());
    bytes
}
fn capsule(bytes: &[u8], media_type: &str) -> (NativeCapsule, Asset) {
    let digest = hex::encode(Sha256::digest(bytes));
    let asset = Asset {
        id: Uuid::from_u128(1),
        name: "Owner original native source".into(),
        media_type: media_type.into(),
        content_sha256: Some(digest.clone()),
        source_revision: Some("user-import-r17".into()),
    };
    let capsule = NativeCapsule {
        id: Uuid::from_u128(2),
        scene_id: Uuid::from_u128(3),
        source_asset_id: asset.id,
        source_sha256: digest,
        label: "Owner creative project".into(),
        fidelity: FidelityReport {
            renderer: "blender".into(),
            renderer_version: "4.0".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Unavailable,
            losses: vec!["Retain original; semantic editing is not admitted".into()],
            evidence_sha256: None,
        },
        editable_parameters: Vec::new(),
        native_editor_hint: "Open original owner application".into(),
    };
    (capsule, asset)
}
fn inspect(
    bytes: &[u8],
    kind: &str,
) -> motionwright_creative_library::Result<AttachedSourceInspection> {
    let (record, asset) = capsule(bytes, kind);
    inspect_attached_native_source(&record, &asset, bytes)
}
fn fixture() -> Vec<u8> {
    original_glb(
        json!({
            "asset":{"version":"2.0","generator":"Owner Blender"},
            "scene":0,
            "scenes":[{"nodes":[0]}],
            "nodes":[{"mesh":0,"name":"OwnerHero","extensions":{"EXT_owner_original":{"blended":true}}}],
            "meshes":[{"primitives":[{"attributes":{"POSITION":0},"material":0}]}],
            "materials":[{"extensions":{"KHR_materials_clearcoat":{"clearcoatFactor":0.7}}}],
            "accessors":[{"bufferView":0,"componentType":5126,"count":3,"type":"VEC3"}],
            "bufferViews":[{"buffer":0,"byteLength":36}],
            "buffers":[{"byteLength":36}],
            "animations":[{"channels":[],"samplers":[]}],
            "cameras":[{"type":"perspective","perspective":{"yfov":0.7,"znear":0.1}}],
            "extensionsUsed":["KHR_materials_clearcoat","EXT_owner_original"],
            "extensionsRequired":["EXT_owner_original"],
            "extras":{"ownerOpaqueEffect":{"value":"NEVER REMOVE THIS"}},
            "otherOriginalRootField":{"nested":[1,2,3]}
        }),
        &[
            (0x004e4942, vec![0_u8; 36]),
            (0x0badcafe, b"foreign bytes...".to_vec()),
        ],
    )
}
#[test]
fn glb_observation_preserves_unknown_root_extensions_and_chunks_without_claiming_native_editability()
 {
    let raw = fixture();
    let report = inspect(&raw, "model/gltf-binary").unwrap();
    assert_eq!(
        report.schema,
        "motionwright.attached-native-source-observation/1"
    );
    assert_eq!(report.source_format, "glb_2_0_structured_attach");
    assert!(report.original_binary_preserved && report.unknown_extensions_preserved);
    assert!(!report.semantically_editable_by_motionwright);
    assert!(!report.runtime_or_imported_code_executed);
    assert!(!report.native_renderer_fidelity_observed);
    assert!(!report.project_snapshot_was_modified && !report.external_rights_or_install_granted);
    assert_eq!(report.glb_chunks.len(), 3);
    assert_eq!(report.glb_chunks[0].chunk_type, "JSON");
    assert_eq!(report.glb_chunks[1].chunk_type, "BIN");
    assert_eq!(report.glb_chunks[2].chunk_type, "0x0badcafe");
    assert!(
        report
            .glb_chunks
            .iter()
            .all(
                |part| part.semantic_editability == AttachedDisposition::OpaquePreserved
                    && part.sha256.len() == 64
            )
    );
    assert_eq!(report.source_bytes, raw.len());
    assert!(
        report
            .unknown_root_fields
            .contains(&"otherOriginalRootField".to_owned())
    );
    assert!(
        report
            .used_extensions
            .contains(&"EXT_owner_original".to_owned())
    );
    assert!(
        report
            .required_extensions
            .contains(&"EXT_owner_original".to_owned())
    );
    assert!(report.properties.iter().any(|part| part.path == "nodes"
        && part.disposition == AttachedDisposition::StructuredObservable
        && part.count == Some(1)));
    assert!(
        report
            .properties
            .iter()
            .any(|part| part.path == "animations"
                && part.disposition == AttachedDisposition::StructuredObservable)
    );
    assert!(
        report
            .properties
            .iter()
            .any(|part| part.path == "extensions/extras/foreign_metadata"
                && part.disposition == AttachedDisposition::OpaquePreserved)
    );
}
#[test]
fn unknown_blender_binary_stays_fully_opaque_and_cannot_be_interpreted_as_an_editable_scene() {
    let source = b"BLENDER-v400 OWN original bytes of project";
    let report = inspect(source, "application/x-blender").unwrap();
    assert_eq!(report.source_format, "blender_project_opaque");
    assert_eq!(report.properties.len(), 1);
    assert_eq!(
        report.properties[0].disposition,
        AttachedDisposition::OpaquePreserved
    );
    assert!(report.glb_chunks.is_empty() && !report.semantically_editable_by_motionwright);
    assert_eq!(report.source_bytes, source.len());
}
#[test]
fn typed_media_type_is_required_before_any_glb_structure_is_exposed() {
    let source = fixture();
    let report = inspect(&source, "application/octet-stream").unwrap();
    assert_eq!(report.source_format, "opaque_unrecognized_original");
    assert!(
        report
            .properties
            .iter()
            .any(|part| part.disposition == AttachedDisposition::Unavailable
                && part.path == "glb_structure")
    );
    assert!(report.glb_chunks.is_empty());
}
#[test]
fn invalid_header_chunk_lengths_or_unknown_version_rejects_typed_inspection() {
    let source = fixture();
    for (offset, bytes) in [
        (4, 3_u32.to_le_bytes()),
        (8, 14_u32.to_le_bytes()),
        (12, 5_u32.to_le_bytes()),
        (16, 0x004e4942_u32.to_le_bytes()),
    ] {
        let mut changed = source.clone();
        changed[offset..offset + 4].copy_from_slice(&bytes);
        assert!(inspect(&changed, "model/gltf-binary").is_err());
    }
    assert!(inspect(b"glTF", "model/gltf-binary").is_err());
    let mut changed = source.clone();
    changed.push(0);
    assert!(inspect(&changed, "model/gltf-binary").is_err());
}
#[test]
fn malformed_extensions_are_not_interpreted_or_elided() {
    for metadata in [
        json!({"asset":{"version":"2.0"},"extensionsUsed":["EXT_a","EXT_a"]}),
        json!({"asset":{"version":"2.0"},"extensionsUsed":["bad/extension"]}),
        json!({"asset":{"version":"2.0"},"extensionsRequired":["EXT_missing"]}),
        json!({"asset":{"version":"3.0"}}),
        json!({"asset":{"version":"2.0"},"nodes":"not a structured collection"}),
    ] {
        let source = original_glb(metadata, &[]);
        assert!(inspect(&source, "model/gltf-binary").is_err());
    }
}
#[test]
fn exact_source_asset_and_capsule_digests_are_mandatory() {
    let source = fixture();
    let (mut capsule, mut asset) = capsule(&source, "model/gltf-binary");
    capsule.source_sha256 = "ab".repeat(32);
    assert!(inspect_attached_native_source(&capsule, &asset, &source).is_err());
    capsule.source_sha256 = asset.content_sha256.clone().unwrap();
    asset.id = Uuid::from_u128(100);
    assert!(inspect_attached_native_source(&capsule, &asset, &source).is_err());
    asset.id = capsule.source_asset_id;
    asset.content_sha256 = Some("cd".repeat(32));
    assert!(inspect_attached_native_source(&capsule, &asset, &source).is_err());
}
#[test]
fn bounded_inspector_never_tries_to_load_arbitrary_large_binary_sources() {
    let source = vec![0_u8; MAX_ATTACHED_INSPECTION_BYTES + 1];
    assert!(inspect(&source, "application/octet-stream").is_err());
}
#[test]
fn a_first_party_data_report_cannot_assert_unearned_external_authority() {
    let source = fixture();
    let report = inspect(&source, "model/gltf-binary").unwrap();
    let mut forged = serde_json::to_value(&report).unwrap();
    forged["execute_command"] = json!("blender -P payload.py");
    assert!(serde_json::from_value::<AttachedSourceInspection>(forged).is_err());
}
