//! Attach-first, byte-preserving source inspection. Never executes imports,
//! rewrites GLB, promotes unknown extensions or grants application access.
use crate::*;
use motionwright_domain::{Asset, NativeCapsule};
use serde_json::{Map, Value};
use std::collections::BTreeSet;

pub const MAX_ATTACHED_INSPECTION_BYTES: usize = 32 * 1024 * 1024;
const MAX_GLB_JSON_BYTES: usize = 4 * 1024 * 1024;
const MAX_GLB_CHUNKS: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AttachedDisposition {
    StructuredObservable,
    TypedEditable,
    OpaquePreserved,
    Unavailable,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AttachedProperty {
    pub path: String,
    pub disposition: AttachedDisposition,
    pub count: Option<u32>,
    pub note: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreservedGlbChunk {
    pub source_offset: usize,
    pub bytes: usize,
    pub chunk_type: String,
    pub sha256: String,
    pub semantic_editability: AttachedDisposition,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AttachedSourceInspection {
    pub schema: String,
    pub capsule_id: Uuid,
    pub scene_id: Uuid,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub source_bytes: usize,
    pub declared_media_type: String,
    pub source_format: String,
    pub properties: Vec<AttachedProperty>,
    pub glb_chunks: Vec<PreservedGlbChunk>,
    pub used_extensions: Vec<String>,
    pub required_extensions: Vec<String>,
    pub unknown_root_fields: Vec<String>,
    pub original_binary_preserved: bool,
    pub unknown_extensions_preserved: bool,
    pub semantically_editable_by_motionwright: bool,
    pub project_snapshot_was_modified: bool,
    pub runtime_or_imported_code_executed: bool,
    pub native_renderer_fidelity_observed: bool,
    pub human_creative_approval: bool,
    pub external_rights_or_install_granted: bool,
}
fn observation(
    path: &str,
    disposition: AttachedDisposition,
    count: Option<u32>,
    note: &str,
) -> AttachedProperty {
    AttachedProperty {
        path: path.into(),
        disposition,
        count,
        note: note.into(),
    }
}
fn field_count(root: &Map<String, Value>, key: &str) -> Result<Option<u32>> {
    match root.get(key) {
        None => Ok(None),
        Some(Value::Array(rows)) => {
            check(
                rows.len() <= 10_000,
                "glTF structured field count exceeds inspection budget",
            )?;
            Ok(Some(rows.len() as u32))
        }
        Some(_) => Err(CraftError(
            "Expected glTF structured field collection".into(),
        )),
    }
}
fn extension_names(root: &Map<String, Value>, key: &str) -> Result<Vec<String>> {
    let Some(value) = root.get(key) else {
        return Ok(Vec::new());
    };
    let values = value
        .as_array()
        .ok_or_else(|| CraftError("glTF extensions must be a string list".into()))?;
    check(
        values.len() <= 256,
        "glTF extension inventory exceeds inspection budget",
    )?;
    let mut unique = BTreeSet::new();
    for item in values {
        let name = item
            .as_str()
            .ok_or_else(|| CraftError("glTF extension name must be a string".into()))?;
        check(
            !name.is_empty()
                && name.len() <= 128
                && name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'),
            "glTF extension identifier has unsupported characters",
        )?;
        check(
            unique.insert(name.to_owned()),
            "Duplicate glTF extension name",
        )?;
    }
    Ok(unique.into_iter().collect())
}
fn parse_glb(source: &[u8], result: &mut AttachedSourceInspection) -> Result<()> {
    check(
        source.len() >= 20 && &source[..4] == b"glTF",
        "Declared glTF binary is missing original GLB magic",
    )?;
    let word = |position: usize| -> u32 {
        u32::from_le_bytes(
            source[position..position + 4]
                .try_into()
                .expect("checked fixed header"),
        )
    };
    check(
        word(4) == 2,
        "Only glTF 2.0 GLB can be inspected structurally",
    )?;
    check(
        usize::try_from(word(8)).ok() == Some(source.len()),
        "GLB declared length differs from exact original bytes",
    )?;
    let (mut cursor, mut count) = (12_usize, 0_usize);
    let mut chunks = Vec::new();
    let mut json_span = None;
    while cursor < source.len() {
        check(
            source.len() - cursor >= 8 && count < MAX_GLB_CHUNKS,
            "GLB chunk structure is truncated or unbounded",
        )?;
        let len = usize::try_from(word(cursor))
            .map_err(|_| CraftError("GLB chunk length over platform budget".into()))?;
        let kind = word(cursor + 4);
        cursor += 8;
        check(
            len > 0 && len % 4 == 0 && len <= source.len() - cursor,
            "GLB chunk length or alignment is invalid",
        )?;
        check(
            (count == 0 && kind == 0x4E4F_534A) || (count != 0 && kind != 0x4E4F_534A),
            "GLB must have exactly one initial JSON chunk",
        )?;
        if count == 0 {
            check(
                len <= MAX_GLB_JSON_BYTES,
                "GLB structured JSON exceeds its inspection budget",
            )?;
            json_span = Some((cursor, cursor + len));
        }
        chunks.push(PreservedGlbChunk {
            source_offset: cursor,
            bytes: len,
            chunk_type: match kind {
                0x4E4F_534A => "JSON".into(),
                0x004E_4942 => "BIN".into(),
                other => format!("0x{other:08x}"),
            },
            sha256: hex::encode(Sha256::digest(&source[cursor..cursor + len])),
            semantic_editability: AttachedDisposition::OpaquePreserved,
        });
        cursor += len;
        count += 1;
    }
    check(
        cursor == source.len() && json_span.is_some(),
        "GLB has trailing bytes or no initial structured JSON",
    )?;
    let (start, end) = json_span.expect("validated initial JSON");
    let json: Value = serde_json::from_slice(&source[start..end])
        .map_err(|_| CraftError("GLB structured JSON is invalid".into()))?;
    let root = json
        .as_object()
        .ok_or_else(|| CraftError("GLB JSON root must be an object".into()))?;
    let asset = root
        .get("asset")
        .and_then(Value::as_object)
        .ok_or_else(|| CraftError("GLB asset version declaration is missing".into()))?;
    check(
        asset.get("version").and_then(Value::as_str) == Some("2.0"),
        "Only original glTF 2.0 metadata is inspected",
    )?;
    let mut props = Vec::new();
    for name in [
        "scenes",
        "nodes",
        "meshes",
        "materials",
        "textures",
        "images",
        "cameras",
        "animations",
        "skins",
        "accessors",
        "bufferViews",
        "buffers",
    ] {
        if let Some(count) = field_count(root, name)? {
            props.push(observation(name,AttachedDisposition::StructuredObservable,Some(count),
                "Original glTF structure is observable; nested semantics, embedded media, actual rendering and Motionwright editability are not established."));
        }
    }
    let used = extension_names(root, "extensionsUsed")?;
    let required = extension_names(root, "extensionsRequired")?;
    check(
        required.iter().all(|name| used.contains(name)),
        "Required glTF extension was omitted from extensionsUsed",
    )?;
    let allowed = [
        "asset",
        "scene",
        "scenes",
        "nodes",
        "meshes",
        "materials",
        "textures",
        "images",
        "cameras",
        "animations",
        "skins",
        "accessors",
        "bufferViews",
        "buffers",
        "extensions",
        "extensionsUsed",
        "extensionsRequired",
        "extras",
        "samplers",
    ];
    let mut unknown = root
        .keys()
        .filter(|key| !allowed.contains(&key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    unknown.sort();
    check(unknown.len() <= 256, "Too many unknown source root fields")?;
    if root.contains_key("extensions")
        || root.contains_key("extras")
        || !used.is_empty()
        || !unknown.is_empty()
    {
        props.push(observation("extensions/extras/foreign_metadata",
            AttachedDisposition::OpaquePreserved,None,
            "All original unknown extensions/extras remain in the content-addressed GLB bytes; no imported code is run or data stripped."));
    }
    if chunks.len() > 1 {
        props.push(observation("binary_chunks",AttachedDisposition::OpaquePreserved,
            Some((chunks.len()-1)as u32),
            "BIN and unknown GLB chunks are opaque original bytes; textures, imported code and semantics are not independently admitted."));
    }
    result.source_format = "glb_2_0_structured_attach".into();
    result.properties = props;
    result.glb_chunks = chunks;
    result.used_extensions = used;
    result.required_extensions = required;
    result.unknown_root_fields = unknown;
    Ok(())
}
/// Byte-preserving preview on an asset already attached by the canonical
/// StudioService. This function cannot produce a project edit or grant.
pub fn inspect_attached_native_source(
    capsule: &NativeCapsule,
    asset: &Asset,
    source: &[u8],
) -> Result<AttachedSourceInspection> {
    check(
        !source.is_empty() && source.len() <= MAX_ATTACHED_INSPECTION_BYTES,
        "Source exceeds 32 MiB inspection budget; its capsule remains opaque",
    )?;
    check(
        asset.id == capsule.source_asset_id
            && asset.content_sha256.as_deref() == Some(capsule.source_sha256.as_str()),
        "Capsule does not point to exact stored asset identity/digest",
    )?;
    check(
        hex::encode(Sha256::digest(source)) == capsule.source_sha256,
        "Stored original bytes differ from capsule digest",
    )?;
    let mut result = AttachedSourceInspection {
        schema: "motionwright.attached-native-source-observation/1".into(),
        capsule_id: capsule.id,
        scene_id: capsule.scene_id,
        source_asset_id: asset.id,
        source_sha256: capsule.source_sha256.clone(),
        source_bytes: source.len(),
        declared_media_type: asset.media_type.clone(),
        source_format: "opaque_unrecognized_original".into(),
        properties: vec![observation(
            "original_source",
            AttachedDisposition::OpaquePreserved,
            None,
            "Original content-addressed source preserved; application behavior and imported code remain unexecuted.",
        )],
        glb_chunks: Vec::new(),
        used_extensions: Vec::new(),
        required_extensions: Vec::new(),
        unknown_root_fields: Vec::new(),
        original_binary_preserved: true,
        unknown_extensions_preserved: true,
        semantically_editable_by_motionwright: false,
        project_snapshot_was_modified: false,
        runtime_or_imported_code_executed: false,
        native_renderer_fidelity_observed: false,
        human_creative_approval: false,
        external_rights_or_install_granted: false,
    };
    if asset.media_type == "model/gltf-binary" {
        parse_glb(source, &mut result)?;
    } else if asset.media_type == "application/x-blender" {
        check(
            source.len() >= 12 && source.starts_with(b"BLENDER"),
            "Declared Blender source is missing original native binary magic",
        )?;
        result.source_format = "blender_project_opaque".into();
        result.properties[0].note="Original Blender source retained byte-identically; its geometry, editable effects and source application behavior are not interpreted or executed.".into();
    } else if source.starts_with(b"glTF") {
        result.properties.push(observation("glb_structure",AttachedDisposition::Unavailable,
            None,"GLB magic detected, but media type does not declare model/gltf-binary; no implicit type coercion or executable import."));
    }
    Ok(result)
}
