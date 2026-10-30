use motionwright_domain::{Asset, Fidelity, FidelityReport, NativeCapsule};
use motionwright_native::attached_source_inspection::{
    AttachedDisposition, AttachedSourceInspection, MAX_ATTACHED_INSPECTION_BYTES,
    inspect_attached_native_source,
};
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
        property_fidelity: Vec::new(),
    };
    (capsule, asset)
}
fn inspect(bytes: &[u8], kind: &str) -> semwright_native_sdk::Result<AttachedSourceInspection> {
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

// Exact first 16 bytes observed in the four preserved physical .blend files.
// This is a header-only unit fixture, not any of those complete source files.
const OBSERVED_COMPRESSED_BLEND_PREFIX: [u8; 16] = [
    0x28, 0xb5, 0x2f, 0xfd, 0xa0, 0x41, 0x06, 0x01, 0x00, 0xbd, 0x37, 0x00, 0x8a, 0x48, 0xcc, 0x11,
];

fn assert_no_native_authority(report: &AttachedSourceInspection) {
    assert!(report.original_binary_preserved && report.unknown_extensions_preserved);
    assert!(!report.semantically_editable_by_motionwright);
    assert!(!report.project_snapshot_was_modified);
    assert!(!report.runtime_or_imported_code_executed);
    assert!(!report.native_renderer_fidelity_observed);
    assert!(!report.human_creative_approval);
    assert!(!report.external_rights_or_install_granted);
    assert!(
        report
            .properties
            .iter()
            .all(|property| property.disposition != AttachedDisposition::TypedEditable)
    );
}

#[test]
fn observed_zstd_header_is_only_a_declared_opaque_blender_source() {
    let source = OBSERVED_COMPRESSED_BLEND_PREFIX;
    let (capsule, asset) = capsule(&source, "application/x-blender");
    let before = (capsule.clone(), asset.clone(), source);
    let report = inspect_attached_native_source(&capsule, &asset, &source).unwrap();
    assert_eq!(report.source_format, "blender_declared_zstd_header_opaque");
    assert_eq!(report.declared_media_type, "application/x-blender");
    assert_eq!(report.source_sha256, hex::encode(Sha256::digest(source)));
    assert_eq!(report.source_bytes, 16);
    assert_eq!(report.properties.len(), 1);
    assert_eq!(report.properties[0].path, "original_source");
    assert_eq!(
        report.properties[0].disposition,
        AttachedDisposition::OpaquePreserved
    );
    assert_eq!(report.properties[0].count, None);
    assert!(report.properties[0].note.contains("without decompression"));
    assert!(
        report.properties[0]
            .note
            .contains("Blender version and semantic validity are not verified")
    );
    assert!(report.glb_chunks.is_empty());
    assert!(report.used_extensions.is_empty() && report.required_extensions.is_empty());
    assert!(report.unknown_root_fields.is_empty());
    assert_no_native_authority(&report);
    assert_eq!((capsule, asset, source), before);
}

#[test]
fn declared_blender_rejects_false_reserved_and_truncated_zstd_headers() {
    // The recorded 0xa0 descriptor requires nine bytes through its frame header.
    for length in 0..9 {
        assert!(
            inspect(
                &OBSERVED_COMPRESSED_BLEND_PREFIX[..length],
                "application/x-blender"
            )
            .is_err()
        );
    }
    let mut false_magic = OBSERVED_COMPRESSED_BLEND_PREFIX;
    false_magic[0] ^= 1;
    assert!(inspect(&false_magic, "application/x-blender").is_err());
    let mut reserved = OBSERVED_COMPRESSED_BLEND_PREFIX;
    reserved[4] |= 0x08;
    assert!(inspect(&reserved, "application/x-blender").is_err());
    // Descriptor-dependent optional fields must be present, without reading them
    // as authority to allocate or decode the advertised payload.
    for (descriptor, required_bytes) in [(0xa1, 10), (0xa2, 11), (0xa3, 13), (0xc3, 18)] {
        let mut truncated = vec![0_u8; required_bytes - 1];
        truncated[..4].copy_from_slice(&OBSERVED_COMPRESSED_BLEND_PREFIX[..4]);
        truncated[4] = descriptor;
        assert!(inspect(&truncated, "application/x-blender").is_err());
    }
    assert!(
        inspect(
            b"\x1f\x8b\x08\x00not-admitted-gzip",
            "application/x-blender"
        )
        .is_err()
    );
    assert!(
        inspect(
            b"\x50\x2a\x4d\x18not-admitted-skippable",
            "application/x-blender"
        )
        .is_err()
    );
    assert!(inspect(b"BLENDER-v40", "application/x-blender").is_err());
}

#[test]
fn zstd_header_observation_does_not_decode_or_validate_payload() {
    let header_only = &OBSERVED_COMPRESSED_BLEND_PREFIX[..9];
    // A complete header does not establish a complete compressed frame. These
    // intentionally incomplete/synthetic bodies must never be called valid Blender.
    let invalid_payload = [header_only, b"not compressed data or a Blender project"].concat();
    let embedded_magic = [header_only, b"BLENDER-v999 unverified payload bytes"].concat();
    let mut huge_declared_size = vec![0x28, 0xb5, 0x2f, 0xfd, 0xe0];
    huge_declared_size.extend_from_slice(&[0xff; 8]);
    for source in [
        header_only,
        invalid_payload.as_slice(),
        embedded_magic.as_slice(),
        huge_declared_size.as_slice(),
    ] {
        let report = inspect(source, "application/x-blender").unwrap();
        assert_eq!(report.source_format, "blender_declared_zstd_header_opaque");
        assert!(
            report.properties[0]
                .note
                .contains("payload, complete-frame structure")
        );
        assert_eq!(report.properties.len(), 1);
        assert_eq!(
            report.properties[0].disposition,
            AttachedDisposition::OpaquePreserved
        );
        assert_no_native_authority(&report);
    }
}

#[test]
fn zstd_observation_requires_declared_mime_and_exact_original_binding() {
    let source = OBSERVED_COMPRESSED_BLEND_PREFIX;
    let generic = inspect(&source, "application/octet-stream").unwrap();
    assert_eq!(generic.source_format, "opaque_unrecognized_original");
    assert_no_native_authority(&generic);
    assert!(inspect(&source, "model/gltf-binary").is_err());
    let (capsule, asset) = capsule(&source, "application/x-blender");
    let mut changed = source;
    changed[15] ^= 1;
    assert!(inspect_attached_native_source(&capsule, &asset, &changed).is_err());
    let mut foreign_asset = asset.clone();
    foreign_asset.id = Uuid::from_u128(99);
    assert!(inspect_attached_native_source(&capsule, &foreign_asset, &source).is_err());
    let mut forged_capsule = capsule;
    forged_capsule.source_sha256 = "00".repeat(32);
    assert!(inspect_attached_native_source(&forged_capsule, &asset, &source).is_err());
}

#[test]
fn preserved_physical_glb_pair_keeps_original_chunks_counts_and_no_editability_claim() {
    // Preserved originals from a physical author/import/edit/reopen cycle.
    // Running this test is source inspection only; it never recreates that native run.
    let originals: [(&[u8], usize, &str, usize, usize, &str); 2] = [
        (
            include_bytes!("fixtures/attached_native_source/authored.glb"),
            1972,
            "a7b485ccb2afc368cf0ea2421d2df098f39ed5e10d632dcc7733bd76ba4f69b1",
            1296,
            1324,
            "438648876739711beb8cc55d6a0070c1b873ce199e0ccac8f0ecc89cfeb3bff8",
        ),
        (
            include_bytes!("fixtures/attached_native_source/edited.glb"),
            1992,
            "61955fda30b99a883f8cd5908b8fc7f9e1dee1168a3460e005c3baf1b753a55c",
            1316,
            1344,
            "924c7f8f9821740ca8c4d171e64b3f565bcc7e0aa3e3a406b7986122f6923792",
        ),
    ];
    for (source, size, digest, json_bytes, binary_offset, json_digest) in originals {
        assert_eq!(source.len(), size);
        assert_eq!(hex::encode(Sha256::digest(source)), digest);
        let report = inspect(source, "model/gltf-binary").unwrap();
        assert_eq!(report.source_format, "glb_2_0_structured_attach");
        assert_eq!(report.source_bytes, size);
        assert_eq!(report.source_sha256, digest);
        let observed = report
            .properties
            .iter()
            .map(|property| (property.path.as_str(), property.disposition, property.count))
            .collect::<Vec<_>>();
        assert_eq!(
            observed,
            vec![
                ("scenes", AttachedDisposition::StructuredObservable, Some(1)),
                ("nodes", AttachedDisposition::StructuredObservable, Some(1)),
                ("meshes", AttachedDisposition::StructuredObservable, Some(1)),
                (
                    "materials",
                    AttachedDisposition::StructuredObservable,
                    Some(1)
                ),
                (
                    "accessors",
                    AttachedDisposition::StructuredObservable,
                    Some(3)
                ),
                (
                    "bufferViews",
                    AttachedDisposition::StructuredObservable,
                    Some(3)
                ),
                (
                    "buffers",
                    AttachedDisposition::StructuredObservable,
                    Some(1)
                ),
                (
                    "binary_chunks",
                    AttachedDisposition::OpaquePreserved,
                    Some(1)
                ),
            ]
        );
        assert!(report.used_extensions.is_empty() && report.required_extensions.is_empty());
        assert!(report.unknown_root_fields.is_empty());
        assert_eq!(report.glb_chunks.len(), 2);
        assert_eq!(report.glb_chunks[0].source_offset, 20);
        assert_eq!(report.glb_chunks[0].bytes, json_bytes);
        assert_eq!(report.glb_chunks[0].chunk_type, "JSON");
        assert_eq!(report.glb_chunks[0].sha256, json_digest);
        assert_eq!(report.glb_chunks[1].source_offset, binary_offset);
        assert_eq!(report.glb_chunks[1].bytes, 648);
        assert_eq!(report.glb_chunks[1].chunk_type, "BIN");
        assert_eq!(
            report.glb_chunks[1].sha256,
            "504e42807417bc2353adfae07813367834ccc3753941d802eb7ed06f5e626d64"
        );
        assert!(
            report
                .glb_chunks
                .iter()
                .all(|chunk| chunk.semantic_editability == AttachedDisposition::OpaquePreserved)
        );
        assert_no_native_authority(&report);
        // Reads must not change the exact original that was admitted above.
        assert_eq!(hex::encode(Sha256::digest(source)), digest);
    }
}
