//! Source-only consumer of Semwright's public Blender Collection profile.
//!
//! This module does not implement a native runtime, grant authority, open a
//! Scene, or claim raster/audio fidelity. Every native command goes through
//! the existing ProductionCoordinator and its original receipt journal.

use super::*;
use crate::multi_renderer::{BlenderPrimitive, BlenderSceneContribution};
use std::collections::BTreeSet;

const SOURCE_SCHEMA: &str = "motionwright-blender-collection-source/1";
const EVIDENCE_SCHEMA: &str = "motionwright-blender-editable-evidence/1";
const SOURCE_CONTRACT_PIN: &str = "54d3459a051adbce4cae9f60c0dea8f60c824be6";
const COLLECTION_MAX_BYTES: u64 = 32 * 1024 * 1024;
const COLLECTION_MAX_ENTITIES: usize = 64; // One unique material per entity.
const SOURCE_FLOAT_TOLERANCE: f64 = 1.0e-5;
const COLLECTION_JOURNAL_MAX_RECEIPTS: u64 = 20_000;
const COLLECTION_JOURNAL_PAGE_SIZE: usize = 256;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableTransform {
    pub translation: [f64; 3],
    pub rotation: [f64; 3],
    pub scale: [f64; 3],
}

impl BlenderEditableTransform {
    fn validate(&self) -> NativeResult<()> {
        for value in self
            .translation
            .iter()
            .chain(&self.rotation)
            .chain(&self.scale)
        {
            if !value.is_finite() || value.abs() > 1_000_000.0 {
                return Err(invalid("Blender editable transform exceeds finite bounds"));
            }
        }
        if self.scale.iter().any(|value| value.abs() < 0.0001) {
            return Err(invalid("Blender editable transform has a degenerate scale"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableTransformDelta {
    pub node_id: Uuid,
    pub transform: BlenderEditableTransform,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum BlenderEditableShape {
    Mesh {
        vertices: Vec<[f64; 3]>,
        faces: Vec<Vec<u32>>,
        uv: Option<Vec<[f64; 2]>>,
    },
    Cylinder {
        radius: f64,
        depth: f64,
        segments: u32,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableMaterial {
    pub id: String,
    pub base_color: [f64; 4],
    pub roughness: f64,
    pub metallic: f64,
    pub opacity: f64,
    pub emission_color: [f64; 4],
    pub emission_strength: f64,
    pub base_color_texture: Option<()>,
    pub roughness_texture: Option<()>,
    pub metallic_texture: Option<()>,
    pub normal_texture: Option<()>,
    pub emission_texture: Option<()>,
    pub opacity_texture: Option<()>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableEntity {
    pub id: String,
    pub name: String,
    pub shape: BlenderEditableShape,
    pub transform: BlenderEditableTransform,
    pub materials: Vec<String>,
    pub modifiers: Vec<()>,
    pub shade_smooth: bool,
    pub attributes: Vec<()>,
}

/// Transport DTO for the static subset, not a copy of Blender's compiler.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableSpec {
    pub version: u32,
    pub collection: String,
    pub meters_per_unit: f64,
    pub textures: Vec<()>,
    pub materials: Vec<BlenderEditableMaterial>,
    pub entities: Vec<BlenderEditableEntity>,
    pub relations: Vec<()>,
    pub animation: Option<()>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableSource {
    pub schema: String,
    /// Identifies the source API examined; this is not a runtime build attestation.
    pub source_contract_semwright_pin: String,
    pub project_resource: String,
    pub canonical_contribution: BlenderSceneContribution,
    /// Explicit native editability probe. Canonical Project import is a separate A3 contract.
    pub native_edits: Vec<BlenderEditableTransformDelta>,
    pub source_sha256: String,
    pub spec: BlenderEditableSpec,
}

fn collection_source(
    resource: &str,
    contribution: BlenderSceneContribution,
    edits: Vec<BlenderEditableTransformDelta>,
) -> NativeResult<BlenderEditableSource> {
    if resource.is_empty() || resource.len() > 256 || edits.len() > 1 {
        return Err(invalid(
            "Blender editable source identity or edit budget is invalid",
        ));
    }
    if contribution.objects.is_empty() || contribution.objects.len() > COLLECTION_MAX_ENTITIES {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Blender editable source requires 1..64 static entities",
        ));
    }
    let mut identities = BTreeSet::new();
    let mut entities = Vec::with_capacity(contribution.objects.len());
    let mut materials = Vec::with_capacity(contribution.objects.len());
    for object in &contribution.objects {
        if !identities.insert(object.node_id) {
            return Err(invalid(
                "Blender editable source contains duplicate logical identities",
            ));
        }
        if object.name.is_empty()
            || object.name.len() > 80
            || object.name.chars().any(char::is_control)
            || object
                .color_rgba
                .iter()
                .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
        {
            return Err(invalid(
                "Blender editable source has invalid name or material channels",
            ));
        }
        let mut transform = BlenderEditableTransform {
            translation: object.location,
            rotation: object.rotation,
            scale: object.scale,
        };
        if let Some(edit) = edits.iter().find(|edit| edit.node_id == object.node_id) {
            transform = edit.transform.clone();
        }
        transform.validate()?;
        let id = format!("node_{}", object.node_id.simple());
        let material_id = format!("mat_{}", object.node_id.simple());
        let shape = match object.primitive {
            BlenderPrimitive::Plane => BlenderEditableShape::Mesh {
                vertices: vec![
                    [-1.0, -1.0, 0.0],
                    [1.0, -1.0, 0.0],
                    [1.0, 1.0, 0.0],
                    [-1.0, 1.0, 0.0],
                ],
                faces: vec![vec![0, 1, 2, 3]],
                uv: None,
            },
            BlenderPrimitive::Cylinder => BlenderEditableShape::Cylinder {
                radius: 1.0,
                depth: 2.0,
                segments: 32,
            },
        };
        materials.push(BlenderEditableMaterial {
            id: material_id.clone(),
            base_color: object.color_rgba,
            roughness: 0.45,
            metallic: 0.0,
            opacity: object.color_rgba[3],
            emission_color: [0.0, 0.0, 0.0, 1.0],
            emission_strength: 0.0,
            base_color_texture: None,
            roughness_texture: None,
            metallic_texture: None,
            normal_texture: None,
            emission_texture: None,
            opacity_texture: None,
        });
        entities.push(BlenderEditableEntity {
            id,
            name: object.name.clone(),
            shape,
            transform,
            materials: vec![material_id],
            modifiers: vec![],
            shade_smooth: false,
            attributes: vec![],
        });
    }
    if edits.iter().any(|edit| !identities.contains(&edit.node_id)) {
        return Err(invalid(
            "Blender edit target is absent from canonical source",
        ));
    }
    let spec = BlenderEditableSpec {
        version: 1,
        collection: format!("scene_{}", contribution.scene_id.simple()),
        meters_per_unit: 1.0,
        textures: vec![],
        materials,
        entities,
        relations: vec![],
        animation: None,
    };
    let body = json!({"resource": resource, "contribution": &contribution, "native_edits": &edits, "spec": &spec});
    let encoded =
        serde_json::to_vec(&body).map_err(|_| invalid("Blender source serialization failed"))?;
    if encoded.len() > MAX_ARGS_BYTES {
        return Err(invalid("Blender editable source exceeds transport budget"));
    }
    Ok(BlenderEditableSource {
        schema: SOURCE_SCHEMA.into(),
        source_contract_semwright_pin: SOURCE_CONTRACT_PIN.into(),
        project_resource: resource.into(),
        canonical_contribution: contribution,
        native_edits: edits,
        source_sha256: format!("{:x}", Sha256::digest(&encoded)),
        spec,
    })
}

/// Pure source validation; this function never dispatches a native command.
pub fn prepare_blender_editable_source(
    resource: &str,
    contribution: BlenderSceneContribution,
) -> NativeResult<BlenderEditableSource> {
    collection_source(resource, contribution, vec![])
}

/// Strict Project admission for this Collection-only profile. Controls outside
/// its source schema must not disappear while an editable proof is produced.
pub fn prepare_blender_editable_project_source(
    project: &motionwright_domain::Project,
    scene_id: Uuid,
) -> NativeResult<BlenderEditableSource> {
    let contribution = build_blender_contribution(project, scene_id)?;
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .ok_or_else(|| invalid("Blender editable scene is absent"))?;
    if scene.camera != motionwright_domain::CameraState::default() {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Blender Collection source cannot preserve an authored planar camera",
        ));
    }
    for node in &scene.nodes {
        if node.text.is_some()
            || node.style.font_family.is_some()
            || node.style.font_size.is_some()
            || node.style.font_weight.is_some()
            || node.style.line_height.is_some()
            || !node.property_locks.is_empty()
        {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Blender Collection source cannot preserve text, font controls or canonical property locks",
            ));
        }
    }
    prepare_blender_editable_source(&project.resource_key(), contribution)
}

fn verify_source(source: &BlenderEditableSource) -> NativeResult<()> {
    let rebuilt = collection_source(
        &source.project_resource,
        source.canonical_contribution.clone(),
        source.native_edits.clone(),
    )?;
    if rebuilt != *source {
        return Err(invalid(
            "Blender source DTO or source digest differs from its bounded projection",
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableSnapshot {
    pub native_session: String,
    pub island: Option<String>,
    pub fingerprint: String,
    pub drift: bool,
    pub total: usize,
    pub items: Vec<Value>,
    pub source_only: bool,
    pub exhaustive: bool,
}

fn local_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn verify_initial_snapshot(value: &Value) -> NativeResult<BlenderEditableSnapshot> {
    let snapshot: BlenderEditableSnapshot = serde_json::from_value(value.clone())
        .map_err(|_| backend("Blender initial snapshot violates its source contract"))?;
    if snapshot.island.is_some()
        || snapshot.native_session.is_empty()
        || snapshot.native_session.len() > 256
        || !is_sha256(&snapshot.fingerprint)
        || snapshot.drift
        || !snapshot.source_only
        || !snapshot.exhaustive
        || snapshot.items.len() != snapshot.total
        || snapshot.total > 512
    {
        return Err(backend(
            "Blender initial whole-scene source inspection is incomplete",
        ));
    }
    Ok(snapshot)
}

fn value_close(value: &Value, expected: f64) -> bool {
    value.as_f64().is_some_and(|observed| {
        observed.is_finite()
            && (observed - expected).abs()
                <= SOURCE_FLOAT_TOLERANCE * observed.abs().max(expected.abs()).max(1.0)
    })
}

fn vector_close<const N: usize>(value: &Value, expected: &[f64; N]) -> bool {
    value.as_array().is_some_and(|rows| {
        rows.len() == N && rows.iter().zip(expected).all(|(v, e)| value_close(v, *e))
    })
}

fn expect_empty(row: &Value, key: &str) -> bool {
    row.get(key)
        .and_then(Value::as_array)
        .is_some_and(Vec::is_empty)
}

fn shader_input<'a>(node: &'a Value, id: &str) -> Option<&'a Value> {
    let rows = node.get("inputs")?.as_array()?;
    let mut matches = rows
        .iter()
        .filter(|row| row.get(0).and_then(Value::as_str) == Some(id));
    let value = matches.next()?.get(1)?;
    matches.next().is_none().then_some(value)
}

fn material_shader_matches(nodes: &[Value], material: &BlenderEditableMaterial) -> bool {
    if nodes.len() != 2 {
        return false;
    }
    let Some(shader) = nodes.iter().find(|node| {
        node.get("name").and_then(Value::as_str) == Some("Principled BSDF")
            && node.get("type").and_then(Value::as_str) == Some("ShaderNodeBsdfPrincipled")
    }) else {
        return false;
    };
    if nodes
        .iter()
        .filter(|node| {
            node.get("name").and_then(Value::as_str) == Some("Material Output")
                && node.get("type").and_then(Value::as_str) == Some("ShaderNodeOutputMaterial")
        })
        .count()
        != 1
    {
        return false;
    }
    [
        ("Roughness", material.roughness),
        ("Metallic", material.metallic),
        ("Alpha", material.opacity),
        ("Emission Strength", material.emission_strength),
    ]
    .iter()
    .all(|(name, expected)| {
        shader_input(shader, name).is_some_and(|value| value_close(value, *expected))
    }) && shader_input(shader, "Base Color")
        .is_some_and(|value| vector_close(value, &material.base_color))
        && shader_input(shader, "Emission Color")
            .is_some_and(|value| vector_close(value, &material.emission_color))
}

/// Validates actual source parameters. Geometry topology requires the independent .blend oracle.
pub fn verify_blender_editable_snapshot(
    source: &BlenderEditableSource,
    value: &Value,
) -> NativeResult<BlenderEditableSnapshot> {
    verify_source(source)?;
    let snapshot: BlenderEditableSnapshot = serde_json::from_value(value.clone())
        .map_err(|_| backend("Blender native snapshot envelope is malformed"))?;
    let island = snapshot
        .island
        .as_deref()
        .filter(|value| local_id(value))
        .ok_or_else(|| backend("Blender managed source has no valid island identity"))?;
    if snapshot.native_session.is_empty()
        || snapshot.native_session.len() > 256
        || !is_sha256(&snapshot.fingerprint)
        || snapshot.drift
        || !snapshot.source_only
        || !snapshot.exhaustive
        || snapshot.total != source.spec.entities.len()
        || snapshot.items.len() != snapshot.total
    {
        return Err(backend(
            "Blender native source is incomplete, stale or outside the source profile",
        ));
    }
    let mut identities = BTreeSet::new();
    let mut names = BTreeSet::new();
    let mut collection = None;
    for row in &snapshot.items {
        let id = row
            .get("entity")
            .and_then(Value::as_str)
            .ok_or_else(|| backend("Blender source row lacks logical entity identity"))?;
        let entity = source
            .spec
            .entities
            .iter()
            .find(|entity| entity.id == id)
            .ok_or_else(|| backend("Blender source contains an unexpected entity"))?;
        let expected_name = format!("SW_{}_{}", &island[..island.len().min(12)], entity.id);
        if !identities.insert(id)
            || row.get("name").and_then(Value::as_str) != Some(expected_name.as_str())
            || !names.insert(expected_name)
            || row.get("native_island").and_then(Value::as_str) != Some(island)
            || row.get("type").and_then(Value::as_str) != Some("MESH")
            || row.get("library") != Some(&json!(false))
            || row.get("data_library") != Some(&json!(false))
            || row.get("hidden_render") != Some(&json!(false))
            || row.get("hidden_viewport") != Some(&json!(false))
            || row.get("parent") != Some(&Value::Null)
            || row.get("parent_name") != Some(&Value::Null)
            || row.get("parent_type").and_then(Value::as_str) != Some("OBJECT")
            || row.get("parent_bone").and_then(Value::as_str) != Some("")
            || row.get("parent_inverse")
                != Some(&json!([
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ]))
            || row.get("drivers").and_then(Value::as_u64) != Some(0)
            || row.get("data_users").and_then(Value::as_u64) != Some(1)
            || row.get("action") != Some(&Value::Null)
            || [
                "modifiers",
                "constraints",
                "actions",
                "nla_tracks",
                "attributes",
            ]
            .iter()
            .any(|key| !expect_empty(row, key))
        {
            return Err(backend(
                "Blender entity identity, ownership or static editability differs",
            ));
        }
        let collections = row
            .get("collections")
            .and_then(Value::as_array)
            .filter(|values| values.len() == 1)
            .ok_or_else(|| backend("Blender source entity does not have one closed Collection"))?;
        let actual_collection = collections[0]
            .as_str()
            .ok_or_else(|| backend("Blender Collection name is invalid"))?;
        let expected_collection = format!(
            "SW_{}_{}",
            &island[..island.len().min(12)],
            source.spec.collection
        );
        if actual_collection != expected_collection
            || collection.is_some_and(|value| value != actual_collection)
        {
            return Err(backend("Blender source Collection identity differs"));
        }
        collection = Some(actual_collection);
        if !vector_close(&row["translation"], &entity.transform.translation)
            || !vector_close(&row["rotation"], &entity.transform.rotation)
            || !vector_close(&row["scale"], &entity.transform.scale)
        {
            return Err(backend(
                "Blender native transform differs from the requested source",
            ));
        }
        let (vertices, polygons) = match &entity.shape {
            BlenderEditableShape::Mesh { .. } => (4, 1),
            BlenderEditableShape::Cylinder { .. } => (64, 34),
        };
        if row.get("vertices").and_then(Value::as_u64) != Some(vertices)
            || row.get("polygons").and_then(Value::as_u64) != Some(polygons)
            || row.get("uv_layers").and_then(Value::as_u64) != Some(0)
            || row.get("smooth_polygons").and_then(Value::as_u64) != Some(0)
            || !row
                .get("geometry_digest")
                .and_then(Value::as_str)
                .is_some_and(is_sha256)
            || !row
                .get("normal_digest")
                .and_then(Value::as_str)
                .is_some_and(is_sha256)
            || row.get("normal_method").and_then(Value::as_str) != Some("source-polygon-normal-v1")
            || row.get("data_name").and_then(Value::as_str)
                != row.get("name").and_then(Value::as_str)
        {
            return Err(backend("Blender source mesh summary differs"));
        }
        let rows = row
            .get("materials")
            .and_then(Value::as_array)
            .filter(|rows| rows.len() == 1)
            .ok_or_else(|| backend("Blender source material slot is missing or duplicated"))?;
        let material = source
            .spec
            .materials
            .iter()
            .find(|material| entity.materials == [material.id.clone()])
            .ok_or_else(|| backend("Blender expected material identity is absent"))?;
        let material_name = format!("SW_{}_{}", &island[..island.len().min(12)], material.id);
        let read = &rows[0];
        let shader_nodes = read
            .get("nodes")
            .and_then(Value::as_array)
            .ok_or_else(|| backend("Blender source material has no shader node readback"))?;
        if read.get("id").and_then(Value::as_str) != Some(material.id.as_str())
            || read.get("name").and_then(Value::as_str) != Some(material_name.as_str())
            || read.get("library") != Some(&json!(false))
            || !vector_close(&read["base_color"], &material.base_color)
            || !vector_close(&read["color"], &material.base_color)
            || !value_close(&read["opacity"], material.opacity)
            || !value_close(&read["roughness"], material.roughness)
            || !value_close(&read["metallic"], material.metallic)
            || !vector_close(&read["emission_color"], &material.emission_color)
            || !value_close(&read["emission_strength"], material.emission_strength)
            || !expect_empty(read, "texture_bindings")
            || !material_shader_matches(shader_nodes, material)
            || read.get("links")
                != Some(&json!([[
                    "Principled BSDF",
                    "BSDF",
                    "Material Output",
                    "Surface"
                ]]))
        {
            return Err(backend(
                "Blender native material or effective Alpha differs",
            ));
        }
    }
    Ok(snapshot)
}

pub fn verify_blender_editable_reopen(
    source: &BlenderEditableSource,
    before: &BlenderEditableSnapshot,
    after: &Value,
) -> NativeResult<BlenderEditableSnapshot> {
    let previous = verify_blender_editable_snapshot(
        source,
        &serde_json::to_value(before)
            .map_err(|_| invalid("Blender prior snapshot serialization failed"))?,
    )?;
    let reopened = verify_blender_editable_snapshot(source, after)?;
    if reopened.native_session == previous.native_session
        || reopened.island != previous.island
        || reopened.fingerprint != previous.fingerprint
        || reopened.items != previous.items
    {
        return Err(backend(
            "Blender reopen did not preserve source in a fresh native process",
        ));
    }
    Ok(reopened)
}

fn verify_transform_delta(
    before: &BlenderEditableSnapshot,
    after: &BlenderEditableSnapshot,
    id: Uuid,
) -> NativeResult<()> {
    let entity = format!("node_{}", id.simple());
    if before.native_session != after.native_session
        || before.island != after.island
        || before.total != after.total
        || before.fingerprint == after.fingerprint
    {
        return Err(backend(
            "Blender transform did not produce a bounded fresh source delta",
        ));
    }
    for original in &before.items {
        let original_id = original.get("entity");
        let mut changed = after
            .items
            .iter()
            .find(|row| row.get("entity") == original_id)
            .ok_or_else(|| backend("Blender edit removed an entity"))?
            .clone();
        if original_id.and_then(Value::as_str) == Some(entity.as_str()) {
            for key in ["translation", "rotation", "scale"] {
                changed[key] = original[key].clone();
            }
        }
        if *original != changed {
            return Err(backend(
                "Blender edit changed source outside the requested transform",
            ));
        }
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BlenderEditablePhase {
    SourcePersisted,
    SourceReopened,
    TransformPersisted,
    EditedSourceReopened,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableStageEvidence {
    pub stage: String,
    pub command: String,
    pub request_id: String,
    pub request_sha256: String,
    pub connection_identity: String,
    pub provider_generation: u64,
    pub descriptor_sha256: String,
    /// Original coordinator response and receipt. Private evidence, never an imported grant.
    pub response: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableArtifact {
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
    pub island: String,
    pub source_fingerprint: String,
    pub inventory: Value,
    pub method: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderEditableEvidence {
    pub schema: String,
    pub phase: BlenderEditablePhase,
    pub source: BlenderEditableSource,
    pub snapshot: BlenderEditableSnapshot,
    pub artifact: BlenderEditableArtifact,
    pub stages: Vec<BlenderEditableStageEvidence>,
    pub source_parameter_readback: bool,
    pub fresh_process_reopened: bool,
    pub native_transform_edit_persisted: bool,
    pub independent_file_readback_verified: bool,
    pub raster_verified: bool,
    pub audio_verified: bool,
    pub full_scene_verified: bool,
    pub canonical_edit_imported: bool,
    pub limitations: Vec<String>,
}

fn limitations() -> Vec<String> {
    [
        "collection_library_only",
        "static_mesh_subset_only",
        "normalized_color_channels_without_raster_colorimetry",
        "display_names_are_bounded_native_aliases",
        "scene_camera_world_render_settings_not_persisted",
        "text_fonts_animation_audio_not_in_profile",
        "independent_blend_topology_readback_required",
        "native_transform_edit_not_imported_into_project",
        "external_asset_rights_and_nle_roundtrip_not_evaluated",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect()
}

fn request_prefix(value: &str) -> NativeResult<()> {
    if value.is_empty()
        || value.len() > 80
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
    {
        return Err(invalid(
            "Blender editable request prefix must be a bounded local identifier",
        ));
    }
    Ok(())
}

fn artifact_name(source: &BlenderEditableSource, request: &str) -> String {
    let attempt = format!("{:x}", Sha256::digest(request.as_bytes()));
    format!(
        "blender-editable-{}-{}-{}.blend",
        source.canonical_contribution.scene_id.simple(),
        &source.source_sha256[..16],
        &attempt[..16]
    )
}

fn preflight_new_artifact(root: &Path, relative: &str) -> NativeResult<()> {
    if relative.len() > 240 || relative.contains(['/', '\\']) || !relative.ends_with(".blend") {
        return Err(invalid(
            "Blender editable artifact must use its generated local filename",
        ));
    }
    let metadata = safe_metadata(root)?;
    if !metadata.is_dir() {
        return Err(invalid(
            "Blender editable output root is not a safe directory",
        ));
    }
    match fs::symlink_metadata(root.join(relative)) {
        Ok(_) => Err(Error::new(
            ErrorCode::Conflict,
            "Blender editable destination already exists",
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(backend("Blender editable destination could not be checked")),
    }
}

fn parse_artifact(
    data: &Value,
    relative: &str,
    snapshot: &BlenderEditableSnapshot,
) -> NativeResult<BlenderEditableArtifact> {
    let artifact = BlenderEditableArtifact {
        relative_path: required_string(data, "/path", "Blender persist omitted path")?,
        sha256: required_string(data, "/sha256", "Blender persist omitted digest")?,
        bytes: data
            .get("bytes")
            .and_then(Value::as_u64)
            .ok_or_else(|| backend("Blender persist omitted byte count"))?,
        island: required_string(data, "/island", "Blender persist omitted island")?,
        source_fingerprint: required_string(
            data,
            "/source_fingerprint",
            "Blender persist omitted source fingerprint",
        )?,
        inventory: data
            .get("inventory")
            .cloned()
            .ok_or_else(|| backend("Blender persist omitted inventory"))?,
        method: required_string(data, "/method", "Blender persist omitted method")?,
    };
    let mut actual_names = artifact
        .inventory
        .get("objects")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Blender persist inventory is invalid"))?
        .clone();
    let mut expected_names: Vec<Value> = snapshot
        .items
        .iter()
        .map(|row| row["name"].clone())
        .collect();
    actual_names.sort_by_key(Value::to_string);
    expected_names.sort_by_key(Value::to_string);
    let collection = snapshot
        .items
        .first()
        .and_then(|row| row.pointer("/collections/0"));
    if artifact.relative_path != relative
        || !is_sha256(&artifact.sha256)
        || artifact.bytes == 0
        || artifact.bytes > COLLECTION_MAX_BYTES
        || Some(artifact.island.as_str()) != snapshot.island.as_deref()
        || artifact.source_fingerprint != snapshot.fingerprint
        || artifact.method != "libraries-write-indirect-closure"
        || data.get("fresh_process_verified") != Some(&json!(false))
        || actual_names != expected_names
        || artifact.inventory.get("collection") != collection
    {
        return Err(backend(
            "Blender persist metadata is not bound to the verified source",
        ));
    }
    Ok(artifact)
}

fn checked_plan(data: &Value, intent: &Value, before: &Value) -> NativeResult<String> {
    let plan_ref = required_string(
        data,
        "/plan_ref",
        "Blender plan omitted its issued reference",
    )?;
    let before_session = required_string(
        before,
        "/native_session",
        "Blender source inspection omitted native session",
    )?;
    let before_fingerprint = required_string(
        before,
        "/fingerprint",
        "Blender source inspection omitted fingerprint",
    )?;
    let owner = required_string(
        data,
        "/plan/body/owner/session",
        "Blender plan omitted its owner binding",
    )?;
    if !local_id(&plan_ref)
        || !data
            .pointer("/plan/digest")
            .and_then(Value::as_str)
            .is_some_and(is_sha256)
        || data.pointer("/plan/body/intent") != Some(intent)
        || !is_sha256(&before_fingerprint)
        || data
            .pointer("/plan/body/profile/id")
            .and_then(Value::as_str)
            != Some("blender-native-authoring")
        || data
            .pointer("/plan/body/profile/version")
            .and_then(Value::as_u64)
            != Some(1)
        || data
            .pointer("/plan/body/base")
            .and_then(Value::as_array)
            .map(Vec::len)
            != Some(1)
        || owner.is_empty()
        || owner.len() > 256
        || data
            .pointer("/plan/body/base/0/key/provider")
            .and_then(Value::as_str)
            != Some("driver:blender")
        || data
            .pointer("/plan/body/base/0/key/resource")
            .and_then(Value::as_str)
            != Some("authoring-workspace")
        || data
            .pointer("/plan/body/base/0/document_id")
            .and_then(Value::as_str)
            != Some("blender-authoring-workspace")
        || data
            .pointer("/plan/body/base/0/provider_session")
            .and_then(Value::as_str)
            != Some(owner.as_str())
        || data
            .pointer("/plan/body/base/0/concurrency")
            .and_then(Value::as_str)
            != Some("best_effort_revalidate")
        || data
            .pointer("/plan/body/base/0/generation")
            .and_then(Value::as_str)
            != Some(before_session.as_str())
        || data
            .pointer("/plan/body/base/0/revision/kind")
            .and_then(Value::as_str)
            != Some("fingerprint")
        || data
            .pointer("/plan/body/base/0/revision/value")
            .and_then(Value::as_str)
            != Some(before_fingerprint.as_str())
    {
        return Err(backend(
            "Blender issued plan is not bound to the exact requested source intent",
        ));
    }
    Ok(plan_ref)
}

fn verified_report(
    data: &Value,
    plan: &Value,
    after: &BlenderEditableSnapshot,
) -> NativeResult<()> {
    use semwright_semantic_composition::{
        Concurrency, EvidenceSource, ExecutionStatus, Revision, SupportLevel, Verdict,
    };
    let report: semwright_semantic_composition::VerificationReport =
        serde_json::from_value(data.clone())
            .map_err(|_| backend("Blender verification report violates the pinned SDK contract"))?;
    let verdict = report
        .verdict()
        .map_err(|_| backend("Blender verification report is invalid"))?;
    let required: BTreeSet<String> = serde_json::from_value(
        plan.pointer("/plan/body/required_rules")
            .cloned()
            .ok_or_else(|| backend("Blender issued plan omitted its required source rules"))?,
    )
    .map_err(|_| backend("Blender issued source rules are malformed"))?;
    let digest = serde_json::to_value(&report.validation.plan_digest)
        .map_err(|_| backend("Blender verification plan digest is malformed"))?;
    let owner = required_string(
        plan,
        "/plan/body/owner/session",
        "Blender issued plan omitted its owner binding",
    )?;
    let mut profile_rules = BTreeSet::from(["blender.plan-native-readback.v1".to_owned()]);
    if plan
        .pointer("/plan/body/intent/kind")
        .and_then(Value::as_str)
        == Some("create")
    {
        profile_rules.insert("blender.unmanaged-preservation.v1".into());
    }
    let readback_scope = plan
        .pointer("/plan/body/changes/operations")
        .and_then(Value::as_array)
        .and_then(|operations| operations.last())
        .and_then(|operation| operation.pointer("/writes/0"))
        .ok_or_else(|| backend("Blender issued plan omitted the source observation address"))?;
    let preservation_scope = json!({"resource":readback_scope.get("resource"),
        "logical_id":"unmanaged-scene", "property":"source-projection"});
    if verdict != Verdict::Pass
        || report.execution_status != ExecutionStatus::Completed
        || report.support_level != SupportLevel::Native
        || !report.effects_unobservable.is_empty()
        || report.effects_observed.is_empty()
        || report.validation.checks.is_empty()
        || Some(&digest) != plan.pointer("/plan/digest")
        || report.validation.required_rules != required
        || required != profile_rules
        || report.validation.base.0.len() != 1
        || report.validation.base.0.iter().any(|base| {
            base.key.provider != "driver:blender"
                || base.key.resource != "authoring-workspace"
                || base.document_id != "blender-authoring-workspace"
                || base.provider_session != owner
                || base.generation != after.native_session
                || base.concurrency != Concurrency::BestEffortRevalidate
                || !matches!(&base.revision, Revision::Fingerprint(_))
        })
        || data
            .pointer("/validation/base/0/revision/value")
            .and_then(Value::as_str)
            != Some(after.fingerprint.as_str())
        || report.validation.checks.iter().any(|check| {
            let preservation = check.rule == "blender.unmanaged-preservation.v1";
            let method = if preservation {
                "blender-unmanaged-source-projection"
            } else {
                "blender-bounded-source-rna"
            };
            let scope = if preservation {
                &preservation_scope
            } else {
                readback_scope
            };
            check.verdict != Verdict::Pass
                || check.version != 1
                || !required.contains(&check.rule)
                || check.evidence.len() != 1
                || check.evidence.iter().any(|observation| {
                    observation.source != EvidenceSource::NativeApi
                        || observation.method != method
                        || observation.method_version != 1
                        || observation.id != format!("obs-{}", check.rule)
                        || observation.artifact.is_some()
                        || serde_json::to_value(&observation.scope).ok().as_ref()
                            != Some(&json!([scope]))
                })
        })
    {
        return Err(backend("Blender required source verification did not pass"));
    }
    Ok(())
}

fn new_evidence(
    phase: BlenderEditablePhase,
    source: BlenderEditableSource,
    snapshot: BlenderEditableSnapshot,
    artifact: BlenderEditableArtifact,
    stages: Vec<BlenderEditableStageEvidence>,
) -> BlenderEditableEvidence {
    let fresh = matches!(
        phase,
        BlenderEditablePhase::SourceReopened | BlenderEditablePhase::EditedSourceReopened
    );
    let edited = !source.native_edits.is_empty();
    BlenderEditableEvidence {
        schema: EVIDENCE_SCHEMA.into(),
        phase,
        source,
        snapshot,
        artifact,
        stages,
        source_parameter_readback: true,
        fresh_process_reopened: fresh,
        native_transform_edit_persisted: edited,
        independent_file_readback_verified: false,
        raster_verified: false,
        audio_verified: false,
        full_scene_verified: false,
        canonical_edit_imported: false,
        limitations: limitations(),
    }
}

fn stage_data(stage: &BlenderEditableStageEvidence) -> NativeResult<&Value> {
    stage
        .response
        .pointer("/receipt/payload/result/data")
        .ok_or_else(|| backend("Blender original completed receipt omitted provider data"))
}

fn expected_stages(phase: &BlenderEditablePhase) -> &'static [&'static str] {
    const STAGES: &[&str] = &[
        "create-inspect",
        "create-plan",
        "create-apply",
        "create-verify",
        "create-readback",
        "create-persist",
        "source-reopen",
        "edit-inspect",
        "edit-plan",
        "edit-apply",
        "edit-verify",
        "edit-readback",
        "edit-persist",
        "edited-reopen",
    ];
    match phase {
        BlenderEditablePhase::SourcePersisted => &STAGES[..6],
        BlenderEditablePhase::SourceReopened => &STAGES[..7],
        BlenderEditablePhase::TransformPersisted => &STAGES[..13],
        BlenderEditablePhase::EditedSourceReopened => STAGES,
    }
}

fn stage_command(stage: &str) -> NativeResult<(&'static str, bool)> {
    match stage {
        "create-inspect" | "create-readback" | "edit-inspect" | "edit-readback" => {
            Ok(("driver.blender.composition.inspect", false))
        }
        "create-plan" | "edit-plan" => Ok(("driver.blender.composition.plan", false)),
        "create-apply" | "edit-apply" => Ok(("driver.blender.composition.apply", true)),
        "create-verify" | "edit-verify" => Ok(("driver.blender.composition.verify", false)),
        "create-persist" | "edit-persist" => Ok(("driver.blender.composition.persist", true)),
        "source-reopen" | "edited-reopen" => Ok(("driver.blender.composition.reopen", true)),
        _ => Err(invalid(
            "Blender evidence contains an unsupported source stage",
        )),
    }
}

fn stage_prefix(stage: &BlenderEditableStageEvidence) -> NativeResult<&str> {
    let suffix = format!("-{}", stage.stage);
    let prefix = stage
        .request_id
        .strip_suffix(&suffix)
        .ok_or_else(|| invalid("Blender evidence stage is not bound to its request prefix"))?;
    request_prefix(prefix)?;
    Ok(prefix)
}

fn stage_args(stage: &BlenderEditableStageEvidence, args: Value) -> NativeResult<()> {
    if stage.request_sha256 != request_digest(&stage.command, &args)? {
        return Err(backend(
            "Blender original receipt arguments differ from the source evidence chain",
        ));
    }
    Ok(())
}

/// Pure custody check. `journal` must come from the application store for authority;
/// supplying arbitrary JSON to this verifier cannot create a grant or dispatch work.
pub fn verify_blender_editable_stage_custody(
    source: &BlenderEditableSource,
    proof: &BlenderEditableStageEvidence,
    journal: &[Value],
) -> NativeResult<()> {
    let (command, mutation) = stage_command(&proof.stage)?;
    stage_prefix(proof)?;
    if proof.command != command
        || !is_sha256(&proof.connection_identity)
        || !is_sha256(&proof.request_sha256)
        || !is_sha256(&proof.descriptor_sha256)
    {
        return Err(invalid(
            "Blender original stage has invalid command or custody identity",
        ));
    }
    let matching: Vec<&Value> = journal
        .iter()
        .filter(|row| {
            row.get("request_id").and_then(Value::as_str) == Some(proof.request_id.as_str())
        })
        .collect();
    let dispatch = matching
        .iter()
        .copied()
        .find(|row| row.get("stage").and_then(Value::as_str) == Some("dispatching"))
        .ok_or_else(|| backend("Blender source dispatch receipt is unavailable"))?;
    let completed = matching
        .iter()
        .copied()
        .find(|row| row.get("stage").and_then(Value::as_str) == Some("completed"))
        .ok_or_else(|| backend("Blender source completion receipt is unavailable"))?;
    let actual_result = completed
        .pointer("/payload/result")
        .ok_or_else(|| backend("Blender original completion omitted its native result"))?;
    if matching.len() != 2
        || proof.response.get("receipt") != Some(completed)
        || proof.response.get("result") != Some(actual_result)
        || proof.response.get("replayed") != Some(&json!(false))
        || dispatch
            .pointer("/payload/connection")
            .and_then(Value::as_str)
            != Some(proof.connection_identity.as_str())
        || dispatch.pointer("/payload/mutation") != Some(&json!(mutation))
        || matching.iter().any(|row| {
            row.get("request_sha256").and_then(Value::as_str) != Some(proof.request_sha256.as_str())
                || row.get("command").and_then(Value::as_str) != Some(command)
                || row.get("project_id") != Some(&json!(source.canonical_contribution.project_id))
                || row.get("generation") != Some(&json!(source.canonical_contribution.generation))
                || row.get("revision") != Some(&json!(source.canonical_contribution.revision))
        })
        || !completed
            .pointer("/payload/broker_request_id")
            .and_then(Value::as_str)
            .is_some_and(|id| !id.is_empty())
        || actual_result.get("provenance_checked") != Some(&json!(true))
        || actual_result
            .pointer("/execution/backend")
            .and_then(Value::as_str)
            != Some("driver:blender")
        || actual_result.pointer("/execution/dry_run") != Some(&json!(false))
        || actual_result
            .pointer("/execution/policy_decision")
            .and_then(Value::as_str)
            != Some("allow")
        || actual_result
            .pointer("/execution/provenance/provider")
            .and_then(Value::as_str)
            != Some("driver:blender")
        || actual_result
            .pointer("/execution/provenance/source")
            .and_then(Value::as_str)
            != Some("driver")
        || actual_result
            .pointer("/execution/provenance/execution_provider")
            .and_then(Value::as_str)
            != Some("driver:blender")
        || actual_result.pointer("/execution/provenance/untrusted_metadata") != Some(&json!(false))
        || actual_result
            .pointer("/execution/provenance/provider_generation")
            .and_then(Value::as_u64)
            != Some(proof.provider_generation)
        || actual_result
            .pointer("/execution/provenance/descriptor_sha256")
            .and_then(Value::as_str)
            != Some(proof.descriptor_sha256.as_str())
    {
        return Err(backend(
            "Blender evidence differs from its original trusted receipt pair",
        ));
    }
    Ok(())
}

/// Reconstructs source/phase/artifact only from the original completed results.
fn verify_evidence_chain(evidence: &BlenderEditableEvidence) -> NativeResult<()> {
    verify_source(&evidence.source)?;
    let sequence = expected_stages(&evidence.phase);
    let fresh = matches!(
        evidence.phase,
        BlenderEditablePhase::SourceReopened | BlenderEditablePhase::EditedSourceReopened
    );
    let edited = matches!(
        evidence.phase,
        BlenderEditablePhase::TransformPersisted | BlenderEditablePhase::EditedSourceReopened
    );
    if evidence.schema != EVIDENCE_SCHEMA
        || evidence.stages.len() != sequence.len()
        || evidence
            .stages
            .iter()
            .zip(sequence)
            .any(|(proof, expected)| proof.stage != *expected)
        || evidence.source.native_edits.len() != usize::from(edited)
        || !evidence.source_parameter_readback
        || evidence.fresh_process_reopened != fresh
        || evidence.native_transform_edit_persisted != edited
        || evidence.independent_file_readback_verified
        || evidence.raster_verified
        || evidence.audio_verified
        || evidence.full_scene_verified
        || evidence.canonical_edit_imported
        || evidence.limitations != limitations()
    {
        return Err(invalid(
            "Blender source evidence phase or coverage differs from its required stage chain",
        ));
    }
    let s = &evidence.stages;
    for (start, end) in [(0, 6), (6, 13), (13, 14)] {
        if start >= s.len() {
            continue;
        }
        let first = &s[start];
        if s[start..end.min(s.len())].iter().any(|stage| {
            stage.connection_identity != first.connection_identity
                || stage.provider_generation != first.provider_generation
        }) {
            return Err(backend(
                "Blender provider or connection changed within a source phase",
            ));
        }
    }
    for (start, end) in [(0, 6), (7, 13)] {
        if start >= s.len() {
            continue;
        }
        let prefix = stage_prefix(&s[start])?;
        for stage in &s[start..end.min(s.len())] {
            if stage_prefix(stage)? != prefix {
                return Err(invalid(
                    "Blender evidence mixes request attempts within a source phase",
                ));
            }
        }
    }
    for (i, first) in s.iter().enumerate() {
        if s[i + 1..].iter().any(|next| {
            next.command == first.command && next.descriptor_sha256 != first.descriptor_sha256
        }) {
            return Err(backend(
                "Blender capability descriptor changed within the editable proof",
            ));
        }
    }
    let original = collection_source(
        &evidence.source.project_resource,
        evidence.source.canonical_contribution.clone(),
        vec![],
    )?;
    stage_args(&s[0], json!({}))?;
    let initial = stage_data(&s[0])?;
    let native_session = verify_initial_snapshot(initial)?.native_session;
    let intent = json!({"kind":"create", "spec": &original.spec});
    stage_args(&s[1], json!({"intent": &intent}))?;
    let plan = checked_plan(stage_data(&s[1])?, &intent, initial)?;
    stage_args(&s[2], json!({"plan_ref": &plan}))?;
    let apply = stage_data(&s[2])?;
    let created = verify_blender_editable_snapshot(&original, &apply["snapshot"])?;
    verified_report(&apply["report"], stage_data(&s[1])?, &created)?;
    let island = created
        .island
        .as_ref()
        .ok_or_else(|| backend("Blender created source omitted island"))?;
    if apply.get("plan_ref").and_then(Value::as_str) != Some(plan.as_str())
        || apply.get("island") != apply["snapshot"].get("island")
        || created.native_session != native_session
    {
        return Err(backend(
            "Blender original apply is not bound to the issued plan and session",
        ));
    }
    stage_args(&s[3], json!({"plan_ref": &plan}))?;
    verified_report(stage_data(&s[3])?, stage_data(&s[1])?, &created)?;
    stage_args(&s[4], json!({"island": island}))?;
    if verify_blender_editable_snapshot(&original, stage_data(&s[4])?)? != created {
        return Err(backend(
            "Blender original independent source readback differs from apply",
        ));
    }
    let original_path = artifact_name(&original, stage_prefix(&s[5])?);
    stage_args(&s[5], json!({"island": island, "path": &original_path}))?;
    let original_artifact = parse_artifact(stage_data(&s[5])?, &original_path, &created)?;
    let mut last_snapshot = created.clone();
    let mut last_artifact = original_artifact.clone();
    if s.len() >= 7 {
        stage_args(
            &s[6],
            json!({"island": island, "path": &original_path, "sha256": &original_artifact.sha256}),
        )?;
        last_snapshot = verify_blender_editable_reopen(&original, &created, stage_data(&s[6])?)?;
    }
    if s.len() >= 13 {
        let reopened = last_snapshot.clone();
        stage_args(&s[7], json!({"island": island}))?;
        if verify_blender_editable_snapshot(&original, stage_data(&s[7])?)? != reopened {
            return Err(backend(
                "Blender edit did not start from the original fresh reopen",
            ));
        }
        let delta = evidence
            .source
            .native_edits
            .first()
            .ok_or_else(|| invalid("Blender edited source omitted its explicit delta"))?;
        let intent = json!({"kind":"transform", "island": island, "entity":format!("node_{}", delta.node_id.simple()),
            "transform": &delta.transform, "meters_per_unit":1.0, "expected_fingerprint": &reopened.fingerprint});
        stage_args(&s[8], json!({"intent": &intent}))?;
        let plan = checked_plan(stage_data(&s[8])?, &intent, stage_data(&s[7])?)?;
        stage_args(&s[9], json!({"plan_ref": &plan}))?;
        let apply = stage_data(&s[9])?;
        if apply.get("plan_ref").and_then(Value::as_str) != Some(plan.as_str())
            || apply.get("island").and_then(Value::as_str) != Some(island.as_str())
        {
            return Err(backend("Blender edit applied a different plan or island"));
        }
        let edited = verify_blender_editable_snapshot(&evidence.source, &apply["snapshot"])?;
        verified_report(&apply["report"], stage_data(&s[8])?, &edited)?;
        verify_transform_delta(&reopened, &edited, delta.node_id)?;
        stage_args(&s[10], json!({"plan_ref": &plan}))?;
        verified_report(stage_data(&s[10])?, stage_data(&s[8])?, &edited)?;
        stage_args(&s[11], json!({"island": island}))?;
        if verify_blender_editable_snapshot(&evidence.source, stage_data(&s[11])?)? != edited {
            return Err(backend(
                "Blender edited source readback differs from its original apply",
            ));
        }
        let path = artifact_name(&evidence.source, stage_prefix(&s[12])?);
        stage_args(&s[12], json!({"island": island, "path": &path}))?;
        last_artifact = parse_artifact(stage_data(&s[12])?, &path, &edited)?;
        if last_artifact.relative_path == original_path
            || last_artifact.sha256 == original_artifact.sha256
        {
            return Err(backend(
                "Blender edited source did not produce a distinct immutable artifact",
            ));
        }
        last_snapshot = edited;
    }
    if s.len() == 14 {
        stage_args(
            &s[13],
            json!({"island": island, "path": &last_artifact.relative_path, "sha256": &last_artifact.sha256}),
        )?;
        last_snapshot =
            verify_blender_editable_reopen(&evidence.source, &last_snapshot, stage_data(&s[13])?)?;
        if last_snapshot.native_session == created.native_session {
            return Err(backend(
                "Blender edited source reused the initial native session",
            ));
        }
    }
    if last_snapshot != evidence.snapshot || last_artifact != evidence.artifact {
        return Err(backend(
            "Blender advertised snapshot or artifact differs from the original completed chain",
        ));
    }
    Ok(())
}

/// Data-only verification against caller-supplied rows. Production callers must
/// use the coordinator, which reads these rows from its own application store.
/// This function never treats an imported journal as execution authority.
pub fn verify_blender_editable_evidence_custody(
    evidence: &BlenderEditableEvidence,
    journal: &[Value],
) -> NativeResult<()> {
    let mut ids = BTreeSet::new();
    for proof in &evidence.stages {
        if !ids.insert(&proof.request_id) {
            return Err(invalid(
                "Blender source evidence duplicated an original request",
            ));
        }
        verify_blender_editable_stage_custody(&evidence.source, proof, journal)?;
    }
    verify_evidence_chain(evidence)
}

fn journal_snapshot_changed() -> Error {
    Error::new(
        ErrorCode::StaleReference,
        "Blender original receipt snapshot changed or is incomplete; reconcile the original source phase without redispatch",
    )
}

fn journal_storage_error(error: motionwright_storage::StorageError) -> Error {
    match error {
        motionwright_storage::StorageError::ReceiptStreamChanged => journal_snapshot_changed(),
        other => storage_error(other),
    }
}

/// Verify custody using only the application's original persisted receipt stream.
/// This read-only check is not a native execution grant or physical-file proof.
/// The coordinator separately verifies the canonical Project and source file.
pub fn verify_blender_editable_stored_evidence_custody(
    service: &StudioService,
    evidence: &BlenderEditableEvidence,
) -> NativeResult<()> {
    // Bound and validate the source/stage chain before reading a journal. The
    // supplied receipt JSON is checked against stored rows, never used as them.
    verify_evidence_chain(evidence)?;
    let requests: BTreeSet<&str> = evidence
        .stages
        .iter()
        .map(|stage| stage.request_id.as_str())
        .collect();
    if requests.len() != evidence.stages.len() {
        return Err(invalid(
            "Blender source evidence duplicated an original request",
        ));
    }
    let project_id = evidence.source.canonical_contribution.project_id;
    let watermark = service
        .production_receipt_watermark(project_id)
        .map_err(journal_storage_error)?;
    if watermark.count > COLLECTION_JOURNAL_MAX_RECEIPTS {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Blender source journal exceeds the bounded 20,000-receipt verification budget; no complete custody claim was made",
        ));
    }
    if (watermark.count == 0) != watermark.latest_id.is_none() {
        return Err(journal_snapshot_changed());
    }

    let mut before = None;
    let mut scanned = 0_u64;
    let mut original_rows = Vec::with_capacity(requests.len() * 2);
    while scanned < watermark.count {
        let page = service
            .production_receipts_snapshot_page(
                project_id,
                &watermark,
                before,
                COLLECTION_JOURNAL_PAGE_SIZE,
            )
            .map_err(journal_storage_error)?;
        if page.is_empty() || page.len() > COLLECTION_JOURNAL_PAGE_SIZE {
            return Err(journal_snapshot_changed());
        }
        for receipt in page {
            if receipt.project_id != project_id
                || watermark.latest_id.is_none_or(|head| receipt.id > head)
                || before.is_some_and(|cursor| receipt.id >= cursor)
            {
                return Err(journal_snapshot_changed());
            }
            before = Some(receipt.id);
            scanned += 1;
            if scanned > watermark.count {
                return Err(journal_snapshot_changed());
            }
            // Do not filter matching requests by generation, command or stage:
            // doing so could hide an UNKNOWN, duplicate or foreign receipt.
            if requests.contains(receipt.request_id.as_str()) {
                original_rows.push(
                    serde_json::to_value(receipt)
                        .map_err(|_| backend("Blender original receipt serialization failed"))?,
                );
                if original_rows.len() > requests.len() * 2 {
                    return Err(backend(
                        "Blender source requests contain extra original journal rows",
                    ));
                }
            }
        }
        // Continue through the complete bounded snapshot even after finding
        // the apparent pairs: an older duplicate/UNKNOWN must remain visible.
    }
    if scanned != watermark.count
        || service
            .production_receipt_watermark(project_id)
            .map_err(journal_storage_error)?
            != watermark
    {
        return Err(journal_snapshot_changed());
    }
    verify_blender_editable_evidence_custody(evidence, &original_rows)?;
    if service
        .production_receipt_watermark(project_id)
        .map_err(journal_storage_error)?
        != watermark
    {
        return Err(journal_snapshot_changed());
    }
    Ok(())
}

impl ProductionCoordinator {
    /// Create and persist one static, editable Collection. This does not render a Scene.
    pub async fn create_blender_editable_collection(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
    ) -> NativeResult<BlenderEditableEvidence> {
        request_prefix(request_id)?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        let source = prepare_blender_editable_project_source(&project, scene_id)?;
        self.blender_editable_current(&source, expected)?;
        let relative = artifact_name(&source, request_id);
        preflight_new_artifact(&self.client.connection().output_root, &relative)?;
        let mut generation = None;
        let mut stages = vec![];
        let before = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "create-inspect",
                "driver.blender.composition.inspect",
                json!({}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        let native_session = verify_initial_snapshot(&before)?.native_session;
        let intent = json!({"kind":"create", "spec": &source.spec});
        let plan = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "create-plan",
                "driver.blender.composition.plan",
                json!({"intent": &intent}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        let plan_ref = checked_plan(&plan, &intent, &before)?;
        let applied = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "create-apply",
                "driver.blender.composition.apply",
                json!({"plan_ref": &plan_ref}),
                true,
                &mut generation,
                &mut stages,
            )
            .await?;
        if applied.get("plan_ref").and_then(Value::as_str) != Some(plan_ref.as_str()) {
            return Err(backend("Blender apply returned another plan reference"));
        }
        let applied_snapshot = verify_blender_editable_snapshot(&source, &applied["snapshot"])?;
        verified_report(
            applied
                .get("report")
                .ok_or_else(|| backend("Blender apply omitted source verification"))?,
            &plan,
            &applied_snapshot,
        )?;
        if applied_snapshot.native_session != native_session
            || applied.get("island") != applied["snapshot"].get("island")
        {
            return Err(backend(
                "Blender create left the original native session or island",
            ));
        }
        let island = applied_snapshot
            .island
            .as_ref()
            .ok_or_else(|| backend("Blender create omitted island"))?;
        let report = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "create-verify",
                "driver.blender.composition.verify",
                json!({"plan_ref": &plan_ref}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        verified_report(&report, &plan, &applied_snapshot)?;
        let readback = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "create-readback",
                "driver.blender.composition.inspect",
                json!({"island": island}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        let snapshot = verify_blender_editable_snapshot(&source, &readback)?;
        if snapshot != applied_snapshot {
            return Err(backend(
                "Blender source drifted between apply and independent inspection",
            ));
        }
        let persisted = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "create-persist",
                "driver.blender.composition.persist",
                json!({"island": island, "path": &relative}),
                true,
                &mut generation,
                &mut stages,
            )
            .await?;
        let artifact = parse_artifact(&persisted, &relative, &snapshot)?;
        self.blender_editable_verify_file(&artifact)?;
        self.blender_editable_current(&source, expected)?;
        let evidence = new_evidence(
            BlenderEditablePhase::SourcePersisted,
            source,
            snapshot,
            artifact,
            stages,
        );
        self.blender_editable_verify_journal(&evidence)?;
        Ok(evidence)
    }

    /// Reopen exact persisted bytes in an owner-provisioned fresh native session.
    /// Importing this evidence never imports plan_refs, policy grants or host handles.
    pub async fn reopen_blender_editable_collection(
        &self,
        expected: &RevisionStamp,
        request_id: &str,
        prior: &BlenderEditableEvidence,
    ) -> NativeResult<BlenderEditableEvidence> {
        request_prefix(request_id)?;
        if !matches!(
            prior.phase,
            BlenderEditablePhase::SourcePersisted | BlenderEditablePhase::TransformPersisted
        ) {
            return Err(invalid(
                "Blender reopen requires a completed persisted source phase",
            ));
        }
        self.blender_editable_current(&prior.source, expected)?;
        self.blender_editable_verify_journal(prior)?;
        self.blender_editable_verify_file(&prior.artifact)?;
        let mut stages = prior.stages.clone();
        let mut generation = None;
        let stage = if prior.phase == BlenderEditablePhase::SourcePersisted {
            "source-reopen"
        } else {
            "edited-reopen"
        };
        let data = self
            .blender_editable_call(
                &prior.source,
                expected,
                request_id,
                stage,
                "driver.blender.composition.reopen",
                json!({"island": &prior.artifact.island,
                "path": &prior.artifact.relative_path, "sha256": &prior.artifact.sha256}),
                true,
                &mut generation,
                &mut stages,
            )
            .await?;
        let snapshot = verify_blender_editable_reopen(&prior.source, &prior.snapshot, &data)?;
        self.blender_editable_verify_file(&prior.artifact)?;
        self.blender_editable_current(&prior.source, expected)?;
        let phase = if prior.phase == BlenderEditablePhase::SourcePersisted {
            BlenderEditablePhase::SourceReopened
        } else {
            BlenderEditablePhase::EditedSourceReopened
        };
        let evidence = new_evidence(
            phase,
            prior.source.clone(),
            snapshot,
            prior.artifact.clone(),
            stages,
        );
        self.blender_editable_verify_journal(&evidence)?;
        Ok(evidence)
    }

    /// Demonstrate a native transform edit after reopening; canonical edit import remains explicit.
    pub async fn edit_blender_reopened_transform(
        &self,
        expected: &RevisionStamp,
        request_id: &str,
        prior: &BlenderEditableEvidence,
        edit: BlenderEditableTransformDelta,
    ) -> NativeResult<BlenderEditableEvidence> {
        request_prefix(request_id)?;
        edit.transform.validate()?;
        if prior.phase != BlenderEditablePhase::SourceReopened
            || !prior.source.native_edits.is_empty()
        {
            return Err(invalid(
                "Blender edit requires the first verified fresh Collection reopen",
            ));
        }
        self.blender_editable_current(&prior.source, expected)?;
        self.blender_editable_verify_journal(prior)?;
        self.blender_editable_verify_file(&prior.artifact)?;
        let source = collection_source(
            &prior.source.project_resource,
            prior.source.canonical_contribution.clone(),
            vec![edit.clone()],
        )?;
        let before_entity = prior
            .source
            .spec
            .entities
            .iter()
            .find(|entity| entity.id == format!("node_{}", edit.node_id.simple()))
            .ok_or_else(|| invalid("Blender edit target is absent from the canonical source"))?;
        if edit.transform == before_entity.transform {
            return Err(invalid(
                "Blender editability probe requires an actual parameter change",
            ));
        }
        let relative = artifact_name(&source, request_id);
        preflight_new_artifact(&self.client.connection().output_root, &relative)?;
        let mut stages = prior.stages.clone();
        let prior_generation = stages
            .last()
            .ok_or_else(|| backend("Blender reopen has no original receipt"))?
            .provider_generation;
        let prior_connection = &stages
            .last()
            .ok_or_else(|| backend("Blender reopen has no original receipt"))?
            .connection_identity;
        if *prior_connection != self.client.connection().identity()? {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Blender reopened Collection belongs to another connection",
            ));
        }
        let mut generation = Some(prior_generation);
        let island = prior
            .snapshot
            .island
            .as_ref()
            .ok_or_else(|| backend("Blender reopened source has no island"))?;
        let current = self
            .blender_editable_call(
                &prior.source,
                expected,
                request_id,
                "edit-inspect",
                "driver.blender.composition.inspect",
                json!({"island": island}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        if verify_blender_editable_snapshot(&prior.source, &current)? != prior.snapshot {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender reopened source changed before the requested edit",
            ));
        }
        let intent = json!({"kind":"transform", "island": island, "entity": format!("node_{}", edit.node_id.simple()),
            "transform": &edit.transform, "meters_per_unit":1.0, "expected_fingerprint": &prior.snapshot.fingerprint});
        let plan = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "edit-plan",
                "driver.blender.composition.plan",
                json!({"intent": &intent}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        let plan_ref = checked_plan(&plan, &intent, &current)?;
        let applied = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "edit-apply",
                "driver.blender.composition.apply",
                json!({"plan_ref": &plan_ref}),
                true,
                &mut generation,
                &mut stages,
            )
            .await?;
        if applied.get("plan_ref").and_then(Value::as_str) != Some(plan_ref.as_str())
            || applied.get("island").and_then(Value::as_str) != Some(island.as_str())
        {
            return Err(backend(
                "Blender transform apply returned another plan or island",
            ));
        }
        let after_apply = verify_blender_editable_snapshot(&source, &applied["snapshot"])?;
        verified_report(
            applied
                .get("report")
                .ok_or_else(|| backend("Blender edit omitted source verification"))?,
            &plan,
            &after_apply,
        )?;
        verify_transform_delta(&prior.snapshot, &after_apply, edit.node_id)?;
        let report = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "edit-verify",
                "driver.blender.composition.verify",
                json!({"plan_ref": &plan_ref}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        verified_report(&report, &plan, &after_apply)?;
        let readback = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "edit-readback",
                "driver.blender.composition.inspect",
                json!({"island": island}),
                false,
                &mut generation,
                &mut stages,
            )
            .await?;
        let snapshot = verify_blender_editable_snapshot(&source, &readback)?;
        if snapshot != after_apply {
            return Err(backend("Blender edited source drifted before persistence"));
        }
        let persisted = self
            .blender_editable_call(
                &source,
                expected,
                request_id,
                "edit-persist",
                "driver.blender.composition.persist",
                json!({"island": island, "path": &relative}),
                true,
                &mut generation,
                &mut stages,
            )
            .await?;
        let artifact = parse_artifact(&persisted, &relative, &snapshot)?;
        self.blender_editable_verify_file(&artifact)?;
        self.blender_editable_verify_file(&prior.artifact)?;
        self.blender_editable_current(&source, expected)?;
        let evidence = new_evidence(
            BlenderEditablePhase::TransformPersisted,
            source,
            snapshot,
            artifact,
            stages,
        );
        self.blender_editable_verify_journal(&evidence)?;
        Ok(evidence)
    }

    fn blender_editable_current(
        &self,
        source: &BlenderEditableSource,
        expected: &RevisionStamp,
    ) -> NativeResult<()> {
        verify_source(source)?;
        let project = self
            .service
            .project(source.canonical_contribution.project_id)
            .map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
            || source.project_resource != project.resource_key()
            || self.client.connection().resource != project.resource_key()
            || source.canonical_contribution
                != prepare_blender_editable_project_source(
                    &project,
                    source.canonical_contribution.scene_id,
                )?
                .canonical_contribution
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender editable canonical source or complete Project stamp changed",
            ));
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)] // Fixed internal stages share one coordinator; no generic UI operation.
    async fn blender_editable_call(
        &self,
        source: &BlenderEditableSource,
        expected: &RevisionStamp,
        prefix: &str,
        stage: &str,
        command: &str,
        args: Value,
        mutation: bool,
        generation: &mut Option<u64>,
        stages: &mut Vec<BlenderEditableStageEvidence>,
    ) -> NativeResult<Value> {
        self.blender_editable_current(source, expected)?;
        let request_id = format!("{prefix}-{stage}");
        let digest = request_digest(command, &args)?;
        let connection = self.client.connection().identity()?;
        let response = self
            .execute(
                source.canonical_contribution.project_id,
                expected,
                &request_id,
                command,
                args,
                mutation,
            )
            .await?;
        // A replay is useful historical evidence, but not a fresh source observation.
        if response.get("replayed") != Some(&json!(false))
            || response.pointer("/result/provenance_checked") != Some(&json!(true))
            || response
                .pointer("/result/execution/backend")
                .and_then(Value::as_str)
                != Some("driver:blender")
            || response.pointer("/result/execution/dry_run") != Some(&json!(false))
            || response
                .pointer("/result/execution/policy_decision")
                .and_then(Value::as_str)
                != Some("allow")
            || response
                .pointer("/result/execution/provenance/provider")
                .and_then(Value::as_str)
                != Some("driver:blender")
            || response
                .pointer("/result/execution/provenance/source")
                .and_then(Value::as_str)
                != Some("driver")
            || response
                .pointer("/result/execution/provenance/execution_provider")
                .and_then(Value::as_str)
                != Some("driver:blender")
            || response.pointer("/result/execution/provenance/untrusted_metadata")
                != Some(&json!(false))
        {
            return Err(backend(
                "Blender source stage lacks fresh, trusted execution provenance",
            ));
        }
        let observed_generation = response
            .pointer("/result/execution/provenance/provider_generation")
            .and_then(Value::as_u64)
            .ok_or_else(|| backend("Blender source stage lacks provider generation"))?;
        let descriptor = required_string(
            &response,
            "/result/execution/provenance/descriptor_sha256",
            "Blender stage lacks descriptor digest",
        )?;
        if !is_sha256(&descriptor) || generation.is_some_and(|prior| prior != observed_generation) {
            return Err(backend(
                "Blender provider generation changed within a source attempt",
            ));
        }
        *generation = Some(observed_generation);
        let receipt = response
            .get("receipt")
            .ok_or_else(|| backend("Blender source stage omitted its original receipt"))?;
        if receipt.get("stage").and_then(Value::as_str) != Some("completed")
            || receipt.get("request_id").and_then(Value::as_str) != Some(request_id.as_str())
            || receipt.get("request_sha256").and_then(Value::as_str) != Some(digest.as_str())
            || receipt.get("command").and_then(Value::as_str) != Some(command)
            || receipt.get("project_id") != Some(&json!(source.canonical_contribution.project_id))
            || receipt.get("generation") != Some(&json!(expected.generation))
            || receipt.get("revision") != Some(&json!(expected.revision))
            || receipt.pointer("/payload/result") != response.get("result")
            || !receipt
                .pointer("/payload/broker_request_id")
                .and_then(Value::as_str)
                .is_some_and(|id| !id.is_empty())
        {
            return Err(backend(
                "Blender source receipt is not bound to this command and Project stamp",
            ));
        }
        let data = response_data(&response)?.clone();
        stages.push(BlenderEditableStageEvidence {
            stage: stage.into(),
            command: command.into(),
            request_id,
            request_sha256: digest,
            connection_identity: connection,
            provider_generation: observed_generation,
            descriptor_sha256: descriptor,
            response,
        });
        Ok(data)
    }

    fn blender_editable_verify_file(&self, artifact: &BlenderEditableArtifact) -> NativeResult<()> {
        let path = verify_output_artifact(
            &self.client.connection().output_root,
            &artifact.relative_path,
            &artifact.sha256,
            COLLECTION_MAX_BYTES,
        )?;
        if safe_metadata(&path)?.len() != artifact.bytes {
            return Err(backend(
                "Blender editable file byte count differs from its original persist receipt",
            ));
        }
        Ok(())
    }

    fn blender_editable_verify_journal(
        &self,
        evidence: &BlenderEditableEvidence,
    ) -> NativeResult<()> {
        verify_blender_editable_stored_evidence_custody(&self.service, evidence)
    }
}
