//! Closed lit-still projection through the existing production coordinator.
//!
//! The composition driver owns authoring and F verification. This adapter owns
//! Project projection, explicit render controls, full-Scene source custody, and
//! bounded phase sequencing. It never runs Blender or Python directly.
//!
//! Each phase requires a fresh isolated canonical Host and a new request identity.
//! Retain the journal on both Ok and Err. No effect is replayed after an error.
//! The path API is not an atomic no-clobber API: a single-writer output root is an
//! admission precondition, and file.save/open still require real Broker authority.

use super::*;
use crate::multi_renderer::{
    BlenderOriginalSceneCamera, BlenderOriginalScenePlan, BlenderPrimitive,
    BlenderSceneContribution, build_blender_original_scene_plan,
};
use semwright_semantic_composition::{Verdict, VerificationReport};
use std::collections::{BTreeMap, BTreeSet};

const LIT_SCHEMA: &str = "motionwright.blender.lit-still/1";
const LIT_PROFILE: &str = "planar-project-cycles-lit-still-v1";
const MAX_LIT_NODES: usize = 126; // Public composition MAX_ENTITIES=128, including camera/light.
const MAX_BLEND_BYTES: u64 = 512 * 1024 * 1024;
const MAX_PNG_BYTES: u64 = 32 * 1024 * 1024;
const ORIGINAL_BLEND: &str = "lit-original.blend";
const EDITED_BLEND: &str = "lit-edited.blend";
const RNA_TOLERANCE: f64 = 1e-6;
const EDIT_TRANSLATION_X: f64 = 0.25;
const LIT_SAMPLES: u32 = 32;

/// These six additions need the existing production command allowlist and actor
/// admission. Declaring them here grants nothing and does not bypass execute().
pub const BLENDER_LIT_REQUIRED_ADDITIONS: &[&str] = &[
    "driver.blender.composition.inspect",
    "driver.blender.composition.plan",
    "driver.blender.composition.apply",
    "driver.blender.composition.verify",
    "driver.blender.render.settings",
    "driver.blender.render",
];

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BlenderLitStillPhase {
    Create,
    Edit,
    Reopen,
    LightControl,
}

impl BlenderLitStillPhase {
    fn label(self) -> &'static str {
        match self {
            Self::Create => "lit-create",
            Self::Edit => "lit-edit",
            Self::Reopen => "lit-reopen",
            Self::LightControl => "lit-light-control",
        }
    }

    fn png(self) -> &'static str {
        match self {
            Self::Create => "lit-original.png",
            Self::Edit => "lit-edited.png",
            Self::Reopen => "lit-reopened.png",
            Self::LightControl => "lit-light-off.png",
        }
    }

    fn output_source(self) -> Option<&'static str> {
        match self {
            Self::Create => Some(ORIGINAL_BLEND),
            Self::Edit => Some(EDITED_BLEND),
            Self::Reopen | Self::LightControl => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BlenderLitArtifact {
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderLitSource {
    pub file: BlenderLitArtifact,
    pub island: String,
    pub composition_fingerprint: String,
    pub origin_native_session: String,
    pub edited_node_id: Option<Uuid>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderLitStillRequest {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub scene_id: Uuid,
    pub request_id: String,
    pub phase: BlenderLitStillPhase,
    pub source: Option<BlenderLitSource>,
    pub edit_node_id: Option<Uuid>,
}

/// Source coordinates and camera contract only. Original-source identity hints
/// are not advertised as composition-generated native names.
#[derive(Debug, Clone, Serialize)]
pub struct BlenderLitProjectProjection {
    pub project_json_sha256: String,
    pub contribution: BlenderSceneContribution,
    pub camera: BlenderOriginalSceneCamera,
}

/// A Project projection, not a claim of native execution or general 3D authoring.
#[derive(Debug, Clone, Serialize)]
pub struct BlenderLitStillPlan {
    pub schema: String,
    pub profile: String,
    pub source: BlenderLitProjectProjection,
    pub scene_name: String,
    pub world_name: String,
    pub camera_entity: String,
    pub light_entity: String,
    pub composition_spec: Value,
    pub light_translation: [f64; 3],
    pub light_rotation: [f64; 3],
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct BlenderLitStillJournal {
    pub schema: String,
    pub phase: String,
    pub plan: Option<Value>,
    pub calls: Vec<Value>,
}

fn unsupported(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

fn local_authoring_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
}

fn native_session_id(value: &str) -> bool {
    !value.is_empty() && value.chars().count() <= 256 && !value.contains('\0')
}

/// Match the public wire contract only. The opaque ref is not a path or a grant;
/// parsing its internal encoding or imposing an ASCII regex would invent a seam.
fn semantic_reference(value: &str) -> bool {
    (16..=1536).contains(&value.chars().count()) && value.starts_with("blender-rna/v1/")
}

fn string_field<'a>(value: &'a Value, key: &str) -> NativeResult<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| backend("Lit-still response omitted a required string"))
}

fn semantic_generation(value: &Value) -> NativeResult<u64> {
    value
        .get("generation")
        .and_then(Value::as_u64)
        .filter(|v| *v > 0)
        .ok_or_else(|| backend("Lit-still response omitted its semantic generation"))
}

fn validate_lit_request(request: &BlenderLitStillRequest) -> NativeResult<()> {
    if request.request_id.is_empty()
        || request.request_id.len() > 48
        || !request
            .request_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
    {
        return Err(invalid("Lit-still request identity is invalid"));
    }
    if request.phase == BlenderLitStillPhase::Create {
        if request.source.is_some() || request.edit_node_id.is_some() {
            return Err(invalid(
                "Lit-still creation cannot consume a source or an edit",
            ));
        }
        return Ok(());
    }
    let source = request
        .source
        .as_ref()
        .ok_or_else(|| invalid("Lit-still reopen phases require a pinned full-Scene source"))?;
    let expected_path = if request.phase == BlenderLitStillPhase::Reopen {
        EDITED_BLEND
    } else {
        ORIGINAL_BLEND
    };
    if source.file.relative_path != expected_path
        || !is_sha256(&source.file.sha256)
        || source.file.bytes == 0
        || source.file.bytes > MAX_BLEND_BYTES
        || !local_authoring_id(&source.island)
        || !is_sha256(&source.composition_fingerprint)
        || !native_session_id(&source.origin_native_session)
    {
        return Err(invalid(
            "Lit-still source binding is outside the closed profile",
        ));
    }
    match request.phase {
        BlenderLitStillPhase::Edit => {
            if request.edit_node_id.is_none() || source.edited_node_id.is_some() {
                return Err(invalid(
                    "Lit-still edit requires an unedited source and one node",
                ));
            }
        }
        BlenderLitStillPhase::Reopen => {
            if request.edit_node_id.is_none() || source.edited_node_id != request.edit_node_id {
                return Err(invalid(
                    "Lit-still reopen must bind the exact persisted node edit",
                ));
            }
        }
        BlenderLitStillPhase::LightControl => {
            if request.edit_node_id.is_some() || source.edited_node_id.is_some() {
                return Err(invalid("Light control consumes the unedited lit source"));
            }
        }
        BlenderLitStillPhase::Create => unreachable!(),
    }
    Ok(())
}

fn node_entity(id: Uuid) -> String {
    format!("node-{}", id.simple())
}

fn material_entity(id: Uuid) -> String {
    format!("mat-{}", id.simple())
}

fn entity_wire(
    id: String,
    name: String,
    shape: Value,
    translation: [f64; 3],
    rotation: [f64; 3],
    scale: [f64; 3],
    materials: Vec<String>,
) -> Value {
    json!({
        "id": id, "name": name, "shape": shape,
        "transform": { "translation": translation, "rotation": rotation, "scale": scale },
        "materials": materials, "modifiers": [], "shade_smooth": false, "attributes": []
    })
}

fn lit_plan_from_source(source: BlenderOriginalScenePlan) -> NativeResult<BlenderLitStillPlan> {
    if source.contribution.objects.is_empty() || source.contribution.objects.len() > MAX_LIT_NODES {
        return Err(unsupported(
            "Lit-still composition supports 1..126 Project nodes plus camera and light",
        ));
    }
    let scene_id = source.contribution.scene_id;
    let camera_entity = format!("camera-{}", scene_id.simple());
    let light_entity = format!("light-{}", scene_id.simple());
    let mut materials = Vec::new();
    let mut entities = Vec::new();
    for object in &source.contribution.objects {
        if object
            .color_rgba
            .iter()
            .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
            || object.color_rgba[3] != 1.0
        {
            return Err(unsupported(
                "Lit-still profile requires an opaque finite source material",
            ));
        }
        let material_id = material_entity(object.node_id);
        materials.push(json!({
            "id": material_id, "base_color": object.color_rgba,
            "roughness": 0.5, "metallic": 0.0,
            "emission_color": [0.0, 0.0, 0.0, 1.0],
            "emission_strength": 0.0, "opacity": 1.0,
            "base_color_texture": null, "roughness_texture": null,
            "metallic_texture": null, "normal_texture": null,
            "emission_texture": null, "opacity_texture": null
        }));
        let shape = match object.primitive {
            BlenderPrimitive::Plane => json!({
                "kind": "mesh",
                "vertices": [[-1.0,-1.0,0.0],[1.0,-1.0,0.0],[1.0,1.0,0.0],[-1.0,1.0,0.0]],
                "faces": [[0,1,2,3]], "uv": null
            }),
            BlenderPrimitive::Cylinder => json!({
                "kind": "cylinder", "radius": 1.0, "depth": 2.0, "segments": 32
            }),
        };
        entities.push(entity_wire(
            node_entity(object.node_id),
            object.name.clone(),
            shape,
            object.location,
            object.rotation,
            object.scale,
            vec![material_id],
        ));
    }
    let camera = &source.camera;
    // Offset the AREA light from the viewing axis; its local -Z faces the scene.
    // This is a declared native lighting realization, not flat Canvas pixel equivalence.
    let light_translation = [
        camera.location[0],
        camera.location[1] + 10.0,
        camera.location[2] - 2.0,
    ];
    let light_rotation = [-10.0_f64.atan2(8.0), 0.0, 0.0];
    if light_translation
        .iter()
        .any(|v| !v.is_finite() || v.abs() > 100_000.0)
    {
        return Err(unsupported(
            "Lit-still light position exceeds its bounded native profile",
        ));
    }
    entities.push(entity_wire(camera_entity.clone(), "Motionwright lit camera".into(),
        json!({"kind":"camera","lens_mm":50.0,"clip_start":camera.clip_start,"clip_end":camera.clip_end}),
        camera.location, camera.rotation_euler, [1.0;3], vec![]));
    entities.push(entity_wire(
        light_entity.clone(),
        "Motionwright area light".into(),
        json!({"kind":"area_light","energy_watts":1000.0,"size":5.0,"color":[1.0,1.0,1.0]}),
        light_translation,
        light_rotation,
        [1.0; 3],
        vec![],
    ));
    let composition_spec = json!({
        "version": 1, "collection": format!("lit-{}", scene_id.simple()),
        "meters_per_unit": 1.0, "textures": [],
        "materials": materials, "entities": entities, "relations": [], "animation": null
    });
    Ok(BlenderLitStillPlan {
        schema: LIT_SCHEMA.into(),
        profile: LIT_PROFILE.into(),
        scene_name: format!("mw-lit-scene-{}", scene_id.simple()),
        world_name: format!("mw-lit-world-{}", scene_id.simple()),
        source: BlenderLitProjectProjection {
            project_json_sha256: source.project_json_sha256,
            contribution: source.contribution,
            camera: source.camera,
        },
        camera_entity,
        light_entity,
        composition_spec,
        light_translation,
        light_rotation,
    })
}

pub fn build_blender_lit_still_plan(
    project: &motionwright_domain::Project,
    scene_id: Uuid,
) -> NativeResult<BlenderLitStillPlan> {
    // Retains all current projection admissions, including non-Normal blend rejection.
    // The existing 512-node original-source profile is not changed.
    lit_plan_from_source(build_blender_original_scene_plan(project, scene_id)?)
}

fn new_lit_output(root: &Path, relative: &str) -> NativeResult<PathBuf> {
    if !matches!(
        relative,
        ORIGINAL_BLEND
            | EDITED_BLEND
            | "lit-original.png"
            | "lit-edited.png"
            | "lit-reopened.png"
            | "lit-light-off.png"
    ) {
        return Err(invalid(
            "Lit-still output name is outside its fixed profile",
        ));
    }
    if !safe_metadata(root)?.is_dir() {
        return Err(invalid("Lit-still output root is not a plain directory"));
    }
    let path = root.join(relative);
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(path),
        Err(_) => Err(backend("Lit-still output path could not be inspected")),
        Ok(_) => Err(Error::new(
            ErrorCode::Conflict,
            "Lit-still output already exists",
        )),
    }
}

fn measure_lit_artifact(
    root: &Path,
    relative: &str,
    maximum: u64,
) -> NativeResult<BlenderLitArtifact> {
    let path = output_artifact_path(root, relative, maximum)?;
    let bytes = safe_metadata(&path)?.len();
    if bytes == 0 {
        return Err(backend("Lit-still native artifact is empty"));
    }
    Ok(BlenderLitArtifact {
        relative_path: relative.into(),
        sha256: sha256_file(&path)?,
        bytes,
    })
}

fn verify_source_binding(root: &Path, source: &BlenderLitSource) -> NativeResult<()> {
    verify_output_artifact(
        root,
        &source.file.relative_path,
        &source.file.sha256,
        MAX_BLEND_BYTES,
    )?;
    if measure_lit_artifact(root, &source.file.relative_path, MAX_BLEND_BYTES)? != source.file {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Lit-still source binding changed",
        ));
    }
    Ok(())
}

fn png_header_matches(header: &[u8], resolution: [u32; 2]) -> bool {
    // Header validation is not decoding, pixel fidelity, or visual acceptance.
    header.len() == 29
        && &header[..8] == b"\x89PNG\r\n\x1a\n"
        && header[8..12] == [0, 0, 0, 13]
        && &header[12..16] == b"IHDR"
        && header[16..20] == resolution[0].to_be_bytes()
        && header[20..24] == resolution[1].to_be_bytes()
        && header[24..29] == [8, 6, 0, 0, 0]
}

fn measure_png(
    root: &Path,
    relative: &str,
    resolution: [u32; 2],
) -> NativeResult<BlenderLitArtifact> {
    let path = output_artifact_path(root, relative, MAX_PNG_BYTES)?;
    let mut header = [0_u8; 29];
    fs::File::open(&path)
        .and_then(|mut f| f.read_exact(&mut header))
        .map_err(|_| backend("Lit-still PNG header could not be read"))?;
    if !png_header_matches(&header, resolution) {
        return Err(backend(
            "Lit-still output is not the requested noninterlaced RGBA8 PNG",
        ));
    }
    measure_lit_artifact(root, relative, MAX_PNG_BYTES)
}

fn rna_values_match(expected: &Value, observed: &Value) -> bool {
    match (expected, observed) {
        (Value::Number(left), Value::Number(right)) if left.is_f64() => {
            match (left.as_f64(), right.as_f64()) {
                (Some(left), Some(right)) => {
                    left.is_finite() && right.is_finite() && (left - right).abs() <= RNA_TOLERANCE
                }
                _ => false,
            }
        }
        (Value::Array(left), Value::Array(right)) => {
            left.len() == right.len() && left.iter().zip(right).all(|(a, b)| rna_values_match(a, b))
        }
        _ => expected == observed,
    }
}

/// This checks a literal value against the returned public descriptor. It does
/// not reinterpret enums, expose arbitrary caller-controlled RNA, or author data.
fn descriptor_admits(descriptor: &Value, value: &Value, write: bool) -> bool {
    let status = descriptor.get("status").and_then(Value::as_str);
    if !matches!(status, Some("managed" | "read_only"))
        || (write
            && (status != Some("managed") || descriptor.get("writable") != Some(&json!(true))))
    {
        return false;
    }
    let Some(length) = descriptor.get("array_length").and_then(Value::as_u64) else {
        return false;
    };
    let Some(kind) = descriptor.get("type").and_then(Value::as_str) else {
        return false;
    };
    let scalar = |value: &Value| -> bool {
        match kind {
            "BOOLEAN" => value.is_boolean(),
            "INT" | "FLOAT" => {
                if kind == "INT" && value.as_i64().is_none() {
                    return false;
                }
                let Some(number) = value.as_f64().filter(|v| v.is_finite()) else {
                    return false;
                };
                for (name, lower) in [("minimum", true), ("maximum", false)] {
                    if let Some(bound) = descriptor.get(name) {
                        let Some(bound) = bound.as_f64().filter(|v| v.is_finite()) else {
                            return false;
                        };
                        if (lower && number < bound) || (!lower && number > bound) {
                            return false;
                        }
                    }
                }
                true
            }
            "ENUM" => value.as_str().is_some_and(|literal| {
                descriptor
                    .get("enum")
                    .and_then(Value::as_array)
                    .is_some_and(|items| {
                        items
                            .iter()
                            .any(|item| item.get("id").and_then(Value::as_str) == Some(literal))
                    })
            }),
            // No free-form RNA strings are needed by this fixed profile.
            _ => false,
        }
    };
    if length == 0 {
        scalar(value)
    } else {
        length <= 16
            && value
                .as_array()
                .is_some_and(|items| items.len() == length as usize && items.iter().all(scalar))
    }
}

fn check_getter(value: &Value, reference: &str, property: &str) -> NativeResult<()> {
    if value.get("ref").and_then(Value::as_str) != Some(reference)
        || value.get("property").and_then(Value::as_str) != Some(property)
        || value.pointer("/descriptor/id").and_then(Value::as_str) != Some(property)
        || !value
            .as_object()
            .is_some_and(|map| map.contains_key("value"))
    {
        return Err(backend(
            "Lit-still getter is not bound to the requested native property",
        ));
    }
    Ok(())
}

fn require_composition_pass(value: &Value) -> NativeResult<()> {
    let report: VerificationReport = serde_json::from_value(value.clone())
        .map_err(|_| backend("Lit-still canonical composition report is malformed"))?;
    let verdict = report
        .verdict()
        .map_err(|_| backend("Lit-still canonical composition report could not be evaluated"))?;
    if verdict != Verdict::Pass {
        return Err(backend(
            "Lit-still canonical composition verification did not pass",
        ));
    }
    Ok(())
}

fn snapshot_header(value: &Value, expected_island: Option<&str>) -> NativeResult<()> {
    let items = value
        .get("items")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Lit-still source snapshot omitted its complete items"))?;
    let island_matches = match expected_island {
        Some(island) => value.get("island").and_then(Value::as_str) == Some(island),
        None => value.get("island") == Some(&Value::Null),
    };
    if !island_matches
        || items.len() > 512
        || value.get("source_only") != Some(&json!(true))
        || value.get("exhaustive") != Some(&json!(true))
        || value.get("total").and_then(Value::as_u64) != Some(items.len() as u64)
        || !value
            .get("fingerprint")
            .and_then(Value::as_str)
            .is_some_and(is_sha256)
        || !value
            .get("native_session")
            .and_then(Value::as_str)
            .is_some_and(native_session_id)
        || value.get("drift").and_then(Value::as_bool).is_none()
    {
        return Err(backend(
            "Lit-still source snapshot is incomplete or outside the public contract",
        ));
    }
    Ok(())
}

fn factory_snapshot(value: &Value) -> NativeResult<()> {
    snapshot_header(value, None)?;
    let expected = BTreeSet::from([
        ("Cube".to_owned(), "MESH".to_owned()),
        ("Camera".to_owned(), "CAMERA".to_owned()),
        ("Light".to_owned(), "LIGHT".to_owned()),
    ]);
    let items = value["items"].as_array().unwrap();
    let observed = items
        .iter()
        .map(|row| {
            Ok((
                string_field(row, "name")?.to_owned(),
                string_field(row, "type")?.to_owned(),
            ))
        })
        .collect::<NativeResult<BTreeSet<_>>>()?;
    if items.len() != 3
        || observed != expected
        || items.iter().any(|row| {
            row.get("native_island") != Some(&Value::Null)
                || row.get("entity") != Some(&Value::Null)
                || row.get("library") != Some(&json!(false))
        })
    {
        return Err(unsupported(
            "Lit-still phases require a fresh isolated factory-startup Host",
        ));
    }
    Ok(())
}

fn snapshot_entity<'a>(snapshot: &'a Value, id: &str) -> NativeResult<&'a Value> {
    let items = snapshot
        .get("items")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Lit-still source snapshot has no entity roster"))?;
    let matches = items
        .iter()
        .filter(|row| row.get("entity").and_then(Value::as_str) == Some(id))
        .collect::<Vec<_>>();
    if matches.len() != 1 {
        return Err(backend(
            "Lit-still full entity identity is missing or ambiguous",
        ));
    }
    Ok(matches[0])
}

fn snapshot_roster(
    plan: &BlenderLitStillPlan,
    snapshot: &Value,
    island: &str,
    edited_node: Option<Uuid>,
    light_control: bool,
) -> NativeResult<String> {
    snapshot_header(snapshot, Some(island))?;
    if snapshot.get("drift") != Some(&json!(light_control)) {
        return Err(backend(
            "Lit-still source drift does not match its explicit phase",
        ));
    }
    let expected = plan.composition_spec["entities"]
        .as_array()
        .ok_or_else(|| backend("Lit-still internal plan has no entity roster"))?;
    let items = snapshot["items"].as_array().unwrap();
    if items.len() != expected.len() {
        return Err(backend("Lit-still native source has unexpected entities"));
    }
    let mut observed_ids = BTreeSet::new();
    let mut names = BTreeSet::new();
    let mut collections = BTreeSet::new();
    for entity in expected {
        let id = string_field(entity, "id")?;
        if !observed_ids.insert(id.to_owned()) {
            return Err(backend("Lit-still plan has duplicate entity identities"));
        }
        let row = snapshot_entity(snapshot, id)?;
        let kind = match entity.pointer("/shape/kind").and_then(Value::as_str) {
            Some("mesh" | "cylinder") => "MESH",
            Some("camera") => "CAMERA",
            Some("area_light") => "LIGHT",
            _ => return Err(backend("Lit-still plan contains an unadmitted shape")),
        };
        let mut translation = entity["transform"]["translation"].clone();
        if edited_node.is_some_and(|node| node_entity(node) == id) {
            let x = translation[0]
                .as_f64()
                .ok_or_else(|| backend("Lit-still edit has no finite source translation"))?;
            translation[0] = json!(x + EDIT_TRANSLATION_X);
        }
        let empty = json!([]);
        if row.get("native_island").and_then(Value::as_str) != Some(island)
            || row.get("type").and_then(Value::as_str) != Some(kind)
            || row.get("library") != Some(&json!(false))
            || row.get("data_library") != Some(&json!(false))
            || row.get("parent") != Some(&Value::Null)
            || row.get("hidden_render") != Some(&json!(false))
            || row.get("hidden_viewport") != Some(&json!(false))
            || row.get("modifiers") != Some(&empty)
            || row.get("constraints") != Some(&empty)
            || row.get("action") != Some(&Value::Null)
            || row.get("actions") != Some(&empty)
            || row.get("nla_tracks") != Some(&empty)
            || row.get("drivers") != Some(&json!(0))
            || !rna_values_match(&translation, &row["translation"])
            || !rna_values_match(&entity["transform"]["rotation"], &row["rotation"])
            || !rna_values_match(&entity["transform"]["scale"], &row["scale"])
            || !names.insert(string_field(row, "name")?.to_owned())
        {
            return Err(backend(
                "Lit-still native entity roster differs from its closed Project projection",
            ));
        }
        let memberships = row
            .get("collections")
            .and_then(Value::as_array)
            .ok_or_else(|| backend("Lit-still entity omitted its collection membership"))?;
        if memberships.len() != 1 {
            return Err(backend(
                "Lit-still native entity has external collection membership",
            ));
        }
        collections.insert(
            memberships[0]
                .as_str()
                .ok_or_else(|| backend("Lit-still native collection name is malformed"))?
                .to_owned(),
        );
        if id == plan.light_entity {
            let energy = if light_control { 0.0 } else { 1000.0 };
            if row.pointer("/light/type").and_then(Value::as_str) != Some("AREA")
                || !rna_values_match(&json!(energy), &row["light"]["energy_watts"])
            {
                return Err(backend(
                    "Lit-still native light does not match its explicit phase",
                ));
            }
        }
    }
    if collections.len() != 1 {
        return Err(backend(
            "Lit-still entities are not in one owned native island collection",
        ));
    }
    Ok(collections.into_iter().next().unwrap())
}

#[derive(Debug, Clone)]
struct RnaAddress {
    root: &'static str,
    name: String,
    pointers: Vec<(&'static str, &'static str)>,
}

impl RnaAddress {
    fn new(root: &'static str, name: &str, pointers: &[(&'static str, &'static str)]) -> Self {
        Self {
            root,
            name: name.into(),
            pointers: pointers.to_vec(),
        }
    }
}

struct RnaGroup {
    address: RnaAddress,
    properties: Vec<(&'static str, Value)>,
    write: bool,
}

fn render_groups(
    plan: &BlenderLitStillPlan,
    snapshot: &Value,
    light_control: bool,
) -> NativeResult<Vec<RnaGroup>> {
    let camera = &plan.source.camera;
    let camera_name = string_field(snapshot_entity(snapshot, &plan.camera_entity)?, "name")?;
    let light_name = string_field(snapshot_entity(snapshot, &plan.light_entity)?, "name")?;
    let scene = |pointers: &[(&'static str, &'static str)]| {
        RnaAddress::new("scenes", &plan.scene_name, pointers)
    };
    let group = |address, properties, write| RnaGroup {
        address,
        properties,
        write,
    };
    Ok(vec![
        group(
            scene(&[]),
            vec![("frame_current", json!(1)), ("frame_subframe", json!(0.0))],
            false,
        ),
        group(
            RnaAddress::new("objects", camera_name, &[("data", "Camera")]),
            vec![
                ("type", json!("ORTHO")),
                ("sensor_fit", json!("VERTICAL")),
                ("ortho_scale", json!(camera.ortho_scale)),
                ("lens", json!(50.0)),
                ("clip_start", json!(camera.clip_start)),
                ("clip_end", json!(camera.clip_end)),
            ],
            true,
        ),
        group(
            RnaAddress::new(
                "objects",
                camera_name,
                &[("data", "Camera"), ("dof", "CameraDOFSettings")],
            ),
            vec![("use_dof", json!(false))],
            true,
        ),
        group(
            RnaAddress::new("objects", light_name, &[("data", "AreaLight")]),
            vec![
                ("type", json!("AREA")),
                ("shape", json!("SQUARE")),
                ("size", json!(5.0)),
                ("color", json!([1.0, 1.0, 1.0])),
                ("energy", json!(if light_control { 0.0 } else { 1000.0 })),
            ],
            true,
        ),
        group(
            RnaAddress::new("worlds", &plan.world_name, &[]),
            vec![
                ("use_nodes", json!(false)),
                ("color", json!([0.0, 0.0, 0.0])),
            ],
            true,
        ),
        group(
            scene(&[("render", "RenderSettings")]),
            vec![
                ("engine", json!("CYCLES")),
                ("resolution_x", json!(camera.resolution[0])),
                ("resolution_y", json!(camera.resolution[1])),
                ("resolution_percentage", json!(100)),
                ("pixel_aspect_x", json!(1.0)),
                ("pixel_aspect_y", json!(1.0)),
                ("film_transparent", json!(false)),
                ("dither_intensity", json!(0.0)),
                ("use_motion_blur", json!(false)),
                ("use_compositing", json!(false)),
                ("use_sequencer", json!(false)),
            ],
            true,
        ),
        group(
            scene(&[("cycles", "CyclesRenderSettings")]),
            vec![
                ("device", json!("CPU")),
                ("samples", json!(LIT_SAMPLES)),
                ("seed", json!(0)),
                ("use_animated_seed", json!(false)),
                ("use_adaptive_sampling", json!(false)),
                ("use_denoising", json!(false)),
            ],
            true,
        ),
        group(
            scene(&[
                ("render", "RenderSettings"),
                ("image_settings", "ImageFormatSettings"),
            ]),
            vec![
                ("file_format", json!("PNG")),
                ("color_mode", json!("RGBA")),
                ("color_depth", json!("8")),
                ("compression", json!(15)),
            ],
            true,
        ),
        group(
            scene(&[("view_settings", "ColorManagedViewSettings")]),
            vec![
                ("view_transform", json!("Standard")),
                ("look", json!("None")),
                ("exposure", json!(0.0)),
                ("gamma", json!(1.0)),
                ("use_curve_mapping", json!(false)),
            ],
            true,
        ),
        group(
            scene(&[("display_settings", "ColorManagedDisplaySettings")]),
            vec![("display_device", json!("sRGB"))],
            true,
        ),
    ])
}

struct LitSession<'a> {
    coordinator: &'a ProductionCoordinator,
    request: &'a BlenderLitStillRequest,
    plan: &'a BlenderLitStillPlan,
    journal: &'a mut BlenderLitStillJournal,
    sequence: u32,
}

impl LitSession<'_> {
    async fn call(&mut self, command: &str, args: Value, mutation: bool) -> NativeResult<Value> {
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| backend("Lit-still call sequence exceeded its bound"))?;
        if self.sequence > 8192 {
            return Err(backend("Lit-still explicit phase exceeded its call budget"));
        }
        let request_id = format!(
            "{}:{}:{:06}",
            self.request.request_id,
            self.request.phase.label(),
            self.sequence
        );
        let index = self.journal.calls.len();
        self.journal.calls.push(json!({
            "sequence":self.sequence,"request_id":request_id,"command":command,
            "args":args.clone(),"mutation":mutation,"native_result":null,"native_error":null
        }));
        match self
            .coordinator
            .execute(
                self.request.project_id,
                &self.request.expected,
                &request_id,
                command,
                args,
                mutation,
            )
            .await
        {
            Ok(value) => {
                self.journal.calls[index]["native_result"] = value.clone();
                response_data(&value)
                    .cloned()
                    .map_err(|error| if mutation { error.uncertain() } else { error })
            }
            Err(error) => {
                self.journal.calls[index]["native_error"] = serde_json::to_value(&error)
                    .unwrap_or_else(|_| json!({"diagnostic_serialization_failed":true}));
                Err(error)
            }
        }
    }

    async fn pages(&mut self, command: &str, mut args: Value) -> NativeResult<(u64, Vec<Value>)> {
        let mut generation = None;
        let mut offset = 0usize;
        let mut rows = Vec::new();
        for _ in 0..9 {
            args["limit"] = json!(256);
            args["offset"] = json!(offset);
            let data = self.call(command, args.clone(), false).await?;
            let observed = semantic_generation(&data)?;
            if generation.is_some_and(|expected| expected != observed)
                || data.get("offset").and_then(Value::as_u64) != Some(offset as u64)
                || (args.get("property").is_some() && data.get("relation") != args.get("property"))
                || (command.ends_with("custom.list") && data.get("ref") != args.get("ref"))
            {
                return Err(backend(
                    "Lit-still inventory lost its reference, generation, or page binding",
                ));
            }
            generation = Some(observed);
            let items = data
                .get("items")
                .and_then(Value::as_array)
                .ok_or_else(|| backend("Lit-still inventory omitted its rows"))?;
            let terminal = data
                .get("truncated")
                .and_then(Value::as_bool)
                .ok_or_else(|| backend("Lit-still inventory omitted its truncation flag"))?;
            if items.len() > 256 || (terminal && items.is_empty()) || offset + items.len() > 2048 {
                return Err(backend(
                    "Lit-still inventory exceeded its bounded exhaustive profile",
                ));
            }
            offset += items.len();
            rows.extend(items.iter().cloned());
            if !terminal {
                return Ok((observed, rows));
            }
        }
        Err(backend("Lit-still inventory remains truncated"))
    }

    async fn lookup(&mut self, root: &str, name: &str) -> NativeResult<String> {
        let (_, rows) = self
            .pages(
                "driver.blender.semantic.objects",
                json!({"root":root,"query":name}),
            )
            .await?;
        let matches = rows
            .iter()
            .filter(|row| row.get("name").and_then(Value::as_str) == Some(name))
            .collect::<Vec<_>>();
        if matches.len() != 1 {
            return Err(backend("Lit-still native name is absent or ambiguous"));
        }
        let reference = string_field(matches[0], "ref")?;
        if !semantic_reference(reference) {
            return Err(backend(
                "Lit-still native reference violates the public wire contract",
            ));
        }
        Ok(reference.into())
    }

    async fn relation(&mut self, reference: &str, property: &str) -> NativeResult<Vec<Value>> {
        let (_, rows) = self
            .pages(
                "driver.blender.semantic.relations",
                json!({"ref":reference,"property":property}),
            )
            .await?;
        if rows.iter().any(|row| {
            !row.get("ref")
                .and_then(Value::as_str)
                .is_some_and(semantic_reference)
        }) {
            return Err(backend(
                "Lit-still relation contains an invalid opaque native reference",
            ));
        }
        Ok(rows)
    }

    async fn pointer(
        &mut self,
        reference: &str,
        property: &str,
        rna_type: &str,
    ) -> NativeResult<String> {
        let rows = self.relation(reference, property).await?;
        if rows.len() != 1 || rows[0].get("rna_type").and_then(Value::as_str) != Some(rna_type) {
            return Err(backend(
                "Lit-still pointer does not identify the required native RNA type",
            ));
        }
        Ok(string_field(&rows[0], "ref")?.into())
    }

    async fn address(&mut self, address: &RnaAddress) -> NativeResult<String> {
        let mut reference = self.lookup(address.root, &address.name).await?;
        for (property, rna_type) in &address.pointers {
            reference = self.pointer(&reference, property, rna_type).await?;
        }
        Ok(reference)
    }

    async fn get(&mut self, reference: &str, property: &str) -> NativeResult<Value> {
        let data = self
            .call(
                "driver.blender.semantic.property.get",
                json!({"ref":reference,"property":property}),
                false,
            )
            .await?;
        check_getter(&data, reference, property)?;
        Ok(data)
    }

    async fn configure_group(&mut self, group: &RnaGroup) -> NativeResult<()> {
        let mut reference = self.address(&group.address).await?;
        // Check every requested literal before the first mutation in this group.
        for (property, value) in &group.properties {
            let getter = self.get(&reference, property).await?;
            if !descriptor_admits(&getter["descriptor"], value, group.write) {
                return Err(unsupported(
                    "Lit-still RNA descriptor does not admit the exact requested value",
                ));
            }
            if !group.write && !rna_values_match(value, &getter["value"]) {
                return Err(unsupported(
                    "Lit-still fresh scene state is outside the fixed still profile",
                ));
            }
        }
        if !group.write {
            return Ok(());
        }
        for (property, value) in &group.properties {
            let data = self
                .call(
                    "driver.blender.semantic.property.set",
                    json!({"ref":reference,"property":property,"value":value}),
                    true,
                )
                .await?;
            if data.get("changed") != Some(&json!(true))
                || data.get("property").and_then(Value::as_str) != Some(*property)
                || !rna_values_match(value, &data["value"])
                || !data
                    .get("ref")
                    .and_then(Value::as_str)
                    .is_some_and(semantic_reference)
            {
                return Err(
                    backend("Lit-still RNA mutation returned an unbound acknowledgement")
                        .uncertain(),
                );
            }
            semantic_generation(&data).map_err(|e| e.uncertain())?;
            reference = string_field(&data, "ref")
                .map_err(|e| e.uncertain())?
                .into();
        }
        Ok(())
    }

    async fn verify_group(&mut self, group: &RnaGroup) -> NativeResult<Vec<Value>> {
        let reference = self.address(&group.address).await?;
        let mut values = Vec::new();
        for (property, expected) in &group.properties {
            let data = self.get(&reference, property).await?;
            if !descriptor_admits(&data["descriptor"], expected, false)
                || !rna_values_match(expected, &data["value"])
            {
                return Err(backend(
                    "Lit-still native render property differs from the declared profile",
                ));
            }
            values.push(data);
        }
        Ok(values)
    }

    async fn relate(
        &mut self,
        owner: &RnaAddress,
        property: &str,
        target: &RnaAddress,
        unlink: bool,
    ) -> NativeResult<()> {
        let reference = self.address(owner).await?;
        let target_reference = self.address(target).await?;
        let command = if unlink {
            "driver.blender.semantic.relation.unlink"
        } else {
            "driver.blender.semantic.relation.set"
        };
        let data = self
            .call(
                command,
                json!({"ref":reference,"property":property,"target_ref":target_reference}),
                true,
            )
            .await?;
        if data.get("changed") != Some(&json!(true))
            || data.get("property").and_then(Value::as_str) != Some(property)
            || !data
                .get("ref")
                .and_then(Value::as_str)
                .is_some_and(semantic_reference)
            || !data
                .get("target_ref")
                .and_then(Value::as_str)
                .is_some_and(semantic_reference)
        {
            return Err(
                backend("Lit-still relation mutation returned an unbound acknowledgement")
                    .uncertain(),
            );
        }
        semantic_generation(&data).map_err(|e| e.uncertain())?;
        Ok(())
    }
}

fn source_tags(
    plan: &BlenderLitStillPlan,
    island: &str,
    fingerprint: &str,
    origin: &str,
    edited_node: Option<Uuid>,
) -> BTreeMap<String, String> {
    let source = &plan.source.contribution;
    BTreeMap::from([
        ("motionwright.profile".into(), LIT_PROFILE.into()),
        (
            "motionwright.project_id".into(),
            source.project_id.to_string(),
        ),
        (
            "motionwright.generation".into(),
            source.generation.to_string(),
        ),
        ("motionwright.revision".into(), source.revision.to_string()),
        ("motionwright.scene_id".into(), source.scene_id.to_string()),
        (
            "motionwright.project_json_sha256".into(),
            plan.source.project_json_sha256.clone(),
        ),
        ("motionwright.island".into(), island.into()),
        ("motionwright.source_fingerprint".into(), fingerprint.into()),
        ("motionwright.origin_native_session".into(), origin.into()),
        (
            "motionwright.edited_node_id".into(),
            edited_node
                .map(|id| id.to_string())
                .unwrap_or_else(|| "none".into()),
        ),
    ])
}

impl LitSession<'_> {
    async fn composition_snapshot(&mut self, island: Option<&str>) -> NativeResult<Value> {
        let args = island
            .map(|id| json!({"island":id}))
            .unwrap_or_else(|| json!({}));
        let value = self
            .call("driver.blender.composition.inspect", args, false)
            .await?;
        snapshot_header(&value, island)?;
        Ok(value)
    }

    async fn apply_intent(
        &mut self,
        intent: Value,
        native_session: &str,
        expected_island: Option<&str>,
    ) -> NativeResult<Value> {
        let planned = self
            .call(
                "driver.blender.composition.plan",
                json!({"intent":intent.clone()}),
                false,
            )
            .await?;
        let plan_ref = string_field(&planned, "plan_ref")?.to_owned();
        if !local_authoring_id(&plan_ref)
            || planned.pointer("/plan/body/intent") != Some(&intent)
            || !planned
                .pointer("/plan/digest")
                .and_then(Value::as_str)
                .is_some_and(is_sha256)
            || planned
                .pointer("/plan/body/profile/id")
                .and_then(Value::as_str)
                != Some("blender-native-authoring")
            || planned
                .pointer("/plan/body/profile/version")
                .and_then(Value::as_u64)
                != Some(1)
        {
            return Err(backend(
                "Lit-still composition plan is not bound to the exact requested intent",
            ));
        }
        let applied = self
            .call(
                "driver.blender.composition.apply",
                json!({"plan_ref":plan_ref}),
                true,
            )
            .await?;
        if applied.get("plan_ref").and_then(Value::as_str) != Some(&plan_ref) {
            return Err(
                backend("Lit-still composition apply returned another plan reference").uncertain(),
            );
        }
        let island = string_field(&applied, "island")
            .map_err(|e| e.uncertain())?
            .to_owned();
        if !local_authoring_id(&island)
            || expected_island.is_some_and(|expected| expected != island)
        {
            return Err(
                backend("Lit-still composition apply returned another native island").uncertain(),
            );
        }
        let snapshot = applied.get("snapshot").ok_or_else(|| {
            backend("Lit-still composition apply omitted its source snapshot").uncertain()
        })?;
        snapshot_header(snapshot, Some(&island)).map_err(|e| e.uncertain())?;
        if snapshot.get("native_session").and_then(Value::as_str) != Some(native_session) {
            return Err(
                backend("Lit-still composition apply changed its actual native session")
                    .uncertain(),
            );
        }
        // The driver owns the F evaluator. This adapter does not recreate its rules or
        // convert completed execution into an acceptance verdict.
        require_composition_pass(&applied["report"])?;
        let verified = self
            .call(
                "driver.blender.composition.verify",
                json!({"plan_ref":plan_ref}),
                false,
            )
            .await?;
        require_composition_pass(&verified)?;
        let observed = self.composition_snapshot(Some(&island)).await?;
        if observed != *snapshot {
            return Err(backend(
                "Lit-still source changed between apply and independent inspect",
            ));
        }
        Ok(observed)
    }

    async fn set_tags(&mut self, tags: &BTreeMap<String, String>) -> NativeResult<()> {
        let mut reference = self.lookup("scenes", &self.plan.scene_name.clone()).await?;
        for (key, value) in tags {
            let result = self
                .call(
                    "driver.blender.semantic.custom.set",
                    json!({"ref":reference,"key":key,"value":value}),
                    true,
                )
                .await?;
            if result.get("changed") != Some(&json!(true))
                || result.get("key").and_then(Value::as_str) != Some(key)
                || result.get("value").and_then(Value::as_str) != Some(value)
                || !result
                    .get("ref")
                    .and_then(Value::as_str)
                    .is_some_and(semantic_reference)
            {
                return Err(backend(
                    "Lit-still source identity write was not acknowledged exactly",
                )
                .uncertain());
            }
            semantic_generation(&result).map_err(|e| e.uncertain())?;
            reference = string_field(&result, "ref")
                .map_err(|e| e.uncertain())?
                .into();
        }
        Ok(())
    }

    async fn verify_tags(&mut self, tags: &BTreeMap<String, String>) -> NativeResult<Value> {
        let reference = self.lookup("scenes", &self.plan.scene_name.clone()).await?;
        let (_, rows) = self
            .pages(
                "driver.blender.semantic.custom.list",
                json!({"ref":reference}),
            )
            .await?;
        let mut values = BTreeMap::new();
        let mut all_keys = BTreeSet::new();
        for row in rows {
            let key = string_field(&row, "key")?;
            if !all_keys.insert(key.to_owned()) {
                return Err(backend(
                    "Lit-still source identity contains a duplicate key",
                ));
            }
            if key.starts_with("motionwright.") {
                if row.get("status").and_then(Value::as_str) != Some("managed") {
                    return Err(backend("Lit-still source identity cannot be read"));
                }
                values.insert(key.to_owned(), string_field(&row, "value")?.to_owned());
            }
        }
        if &values != tags {
            return Err(backend(
                "Lit-still full-Scene identity differs from its pinned Project/source binding",
            ));
        }
        serde_json::to_value(values).map_err(|_| backend("Lit-still identity could not be encoded"))
    }

    async fn setup_scene(&mut self, snapshot: &Value, island: &str) -> NativeResult<()> {
        let collection = snapshot_roster(self.plan, snapshot, island, None, false)?;
        let active = self
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        if active.get("name").and_then(Value::as_str) != Some("Scene") {
            return Err(unsupported(
                "Lit-still source creation requires the admitted factory Scene",
            ));
        }
        let scene_ref = self.lookup("scenes", "Scene").await?;
        let scene_name = self.plan.scene_name.clone();
        let renamed = self
            .call(
                "driver.blender.semantic.rename",
                json!({"ref":scene_ref,"name":scene_name}),
                true,
            )
            .await?;
        if renamed.get("name").and_then(Value::as_str) != Some(&scene_name)
            || renamed.get("changed") != Some(&json!(true))
            || !renamed
                .get("ref")
                .and_then(Value::as_str)
                .is_some_and(semantic_reference)
        {
            return Err(backend("Lit-still Scene rename was not acknowledged exactly").uncertain());
        }
        semantic_generation(&renamed).map_err(|e| e.uncertain())?;
        let scene = RnaAddress::new("scenes", &scene_name, &[]);
        let master = RnaAddress::new("scenes", &scene_name, &[("collection", "Collection")]);
        let master_ref = self.address(&master).await?;
        let children = self.relation(&master_ref, "children").await?;
        if children
            .iter()
            .filter(|row| row.get("name").and_then(Value::as_str) == Some(&collection))
            .count()
            != 1
        {
            return Err(backend(
                "Lit-still composition collection is not linked to the active Scene",
            ));
        }
        for row in children {
            let name = string_field(&row, "name")?;
            if name != collection {
                self.relate(
                    &master,
                    "children",
                    &RnaAddress::new("collections", name, &[]),
                    true,
                )
                .await?;
            }
        }
        let master_ref = self.address(&master).await?;
        for row in self.relation(&master_ref, "objects").await? {
            self.relate(
                &master,
                "objects",
                &RnaAddress::new("objects", string_field(&row, "name")?, &[]),
                true,
            )
            .await?;
        }
        let camera_name =
            string_field(snapshot_entity(snapshot, &self.plan.camera_entity)?, "name")?;
        self.relate(
            &scene,
            "camera",
            &RnaAddress::new("objects", camera_name, &[]),
            false,
        )
        .await?;
        let world_name = self.plan.world_name.clone();
        let world = self
            .call(
                "driver.blender.semantic.datablock.create",
                json!({"root":"worlds","name":world_name}),
                true,
            )
            .await?;
        if world.get("name").and_then(Value::as_str) != Some(&world_name)
            || world.get("root").and_then(Value::as_str) != Some("worlds")
            || world.get("rna_type").and_then(Value::as_str) != Some("World")
            || world.get("changed") != Some(&json!(true))
            || !world
                .get("ref")
                .and_then(Value::as_str)
                .is_some_and(semantic_reference)
        {
            return Err(
                backend("Lit-still world creation was not acknowledged exactly").uncertain(),
            );
        }
        semantic_generation(&world).map_err(|e| e.uncertain())?;
        self.relate(
            &scene,
            "world",
            &RnaAddress::new("worlds", &world_name, &[]),
            false,
        )
        .await?;
        let camera = &self.plan.source.camera;
        let settings = self
            .call(
                "driver.blender.render.settings",
                json!({"width":camera.resolution[0],"height":camera.resolution[1],
                "samples":LIT_SAMPLES,"engine":"CYCLES","device":"CPU","backend":"AUTO"}),
                true,
            )
            .await?;
        if settings
            != json!({"changed":true,"engine":"CYCLES","device":"CPU",
            "backend":null,"devices":[],"fallback":null})
        {
            return Err(backend(
                "Lit-still render settings selected an unrequested device or fallback",
            )
            .uncertain());
        }
        for group in render_groups(self.plan, snapshot, false)? {
            self.configure_group(&group).await?;
        }
        Ok(())
    }

    async fn membership(
        &mut self,
        address: &RnaAddress,
        property: &str,
        expected: &BTreeSet<String>,
        rna_type: &str,
    ) -> NativeResult<Value> {
        let reference = self.address(address).await?;
        let rows = self.relation(&reference, property).await?;
        let observed = rows
            .iter()
            .map(|row| string_field(row, "name").map(str::to_owned))
            .collect::<NativeResult<BTreeSet<_>>>()?;
        if rows.len() != expected.len()
            || &observed != expected
            || rows
                .iter()
                .any(|row| row.get("rna_type").and_then(Value::as_str) != Some(rna_type))
        {
            return Err(backend(
                "Lit-still native membership differs from the declared closed Scene",
            ));
        }
        Ok(json!({"property":property,"items":rows}))
    }

    async fn readback(
        &mut self,
        island: &str,
        native_session: &str,
        fingerprint: &str,
        edited_node: Option<Uuid>,
        light_control: bool,
        tags: &BTreeMap<String, String>,
    ) -> NativeResult<Value> {
        let snapshot = self.composition_snapshot(Some(island)).await?;
        let collection = snapshot_roster(self.plan, &snapshot, island, edited_node, light_control)?;
        if snapshot.get("native_session").and_then(Value::as_str) != Some(native_session)
            || snapshot.get("fingerprint").and_then(Value::as_str) != Some(fingerprint)
        {
            return Err(backend(
                "Lit-still source fingerprint or actual native session changed",
            ));
        }
        let mut properties = Vec::new();
        for group in render_groups(self.plan, &snapshot, light_control)? {
            properties.extend(self.verify_group(&group).await?);
        }
        let scene_name = self.plan.scene_name.clone();
        let scene = RnaAddress::new("scenes", &scene_name, &[]);
        let master = RnaAddress::new("scenes", &scene_name, &[("collection", "Collection")]);
        let collection_address = RnaAddress::new("collections", &collection, &[]);
        let expected_names = snapshot["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| string_field(row, "name").map(str::to_owned))
            .collect::<NativeResult<BTreeSet<_>>>()?;
        let camera_name = string_field(
            snapshot_entity(&snapshot, &self.plan.camera_entity)?,
            "name",
        )?
        .to_owned();
        let scene_members = self
            .membership(&scene, "objects", &expected_names, "Object")
            .await?;
        let collection_members = self
            .membership(&collection_address, "objects", &expected_names, "Object")
            .await?;
        let children = self
            .membership(
                &master,
                "children",
                &BTreeSet::from([collection.clone()]),
                "Collection",
            )
            .await?;
        let master_objects = self
            .membership(&master, "objects", &BTreeSet::new(), "Object")
            .await?;
        let nested_collections = self
            .membership(
                &collection_address,
                "children",
                &BTreeSet::new(),
                "Collection",
            )
            .await?;
        let active_camera = self
            .membership(
                &scene,
                "camera",
                &BTreeSet::from([camera_name.clone()]),
                "Object",
            )
            .await?;
        let active_world = self
            .membership(
                &scene,
                "world",
                &BTreeSet::from([self.plan.world_name.clone()]),
                "World",
            )
            .await?;
        let source_identity = self.verify_tags(tags).await?;
        let active_scene = self
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        if active_scene.get("name").and_then(Value::as_str) != Some(&scene_name)
            || active_scene.get("camera").and_then(Value::as_str) != Some(&camera_name)
            || active_scene.get("object_count").and_then(Value::as_u64)
                != Some(expected_names.len() as u64)
            || active_scene.get("resolution") != Some(&json!(self.plan.source.camera.resolution))
        {
            return Err(backend(
                "Lit-still active Scene differs from its source and render profile",
            ));
        }
        // Bracket all getters with another complete source observation. The
        // property responses themselves also bind their exact opaque RNA refs.
        let after = self.composition_snapshot(Some(island)).await?;
        if after != snapshot {
            return Err(backend("Lit-still native source changed during readback"));
        }
        Ok(json!({
            "snapshot":snapshot,"properties":properties,"source_identity":source_identity,
            "scene_members":scene_members,"collection_members":collection_members,
            "master_children":children,"master_objects":master_objects,
            "nested_collections":nested_collections,"active_camera":active_camera,
            "active_world":active_world,"active_scene":active_scene,
            "light_control":light_control,"pixel_fidelity_verified":false
        }))
    }
}

impl ProductionCoordinator {
    /// Execute exactly one closed phase in a newly admitted isolated canonical
    /// Blender context. The caller must keep the journal even if this returns Err.
    /// A native file is measured only after the exact provider acknowledgement.
    /// Decoder, before/after pixel comparison, custody selection, and formal acceptance
    /// remain independent gates; PNG header inspection does not satisfy them.
    pub async fn realize_blender_lit_still(
        &self,
        request: &BlenderLitStillRequest,
        journal: &mut BlenderLitStillJournal,
    ) -> NativeResult<Value> {
        validate_lit_request(request)?;
        if !journal.schema.is_empty()
            || !journal.phase.is_empty()
            || journal.plan.is_some()
            || !journal.calls.is_empty()
        {
            return Err(invalid(
                "Lit-still journal must belong to one new explicit attempt",
            ));
        }
        let project = self
            .service
            .project(request.project_id)
            .map_err(storage_error)?;
        if project.stamp() != request.expected
            || self.client.connection().resource != project.resource_key()
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Lit-still Project authority changed",
            ));
        }
        let plan = build_blender_lit_still_plan(&project, request.scene_id)?;
        if request.edit_node_id.is_some_and(|id| {
            !plan
                .source
                .contribution
                .objects
                .iter()
                .any(|object| object.node_id == id)
        }) {
            return Err(invalid(
                "Lit-still edit does not identify a source Canvas node",
            ));
        }
        let first_request = format!("{}:{}:000001", request.request_id, request.phase.label());
        if self
            .service
            .latest_production_receipt(request.project_id, &first_request)
            .map_err(storage_error)?
            .is_some()
        {
            return Err(Error::new(ErrorCode::Conflict,
                "Lit-still phase was already attempted; inspect the original receipts without replay").uncertain());
        }
        let scoped = self.with_scene_without_film_options(
            &project,
            request.scene_id,
            motionwright_domain::RendererKind::Blender,
        )?;
        let output_root = self.client.connection().output_root.clone();
        new_lit_output(&output_root, request.phase.png())?;
        if let Some(name) = request.phase.output_source() {
            new_lit_output(&output_root, name)?;
        }
        if let Some(source) = &request.source {
            verify_source_binding(&output_root, source)?;
        }
        journal.schema = LIT_SCHEMA.into();
        journal.phase = request.phase.label().into();
        journal.plan = Some(
            serde_json::to_value(&plan)
                .map_err(|_| backend("Lit-still Project plan could not be encoded"))?,
        );
        let mut session = LitSession {
            coordinator: &scoped,
            request,
            plan: &plan,
            journal,
            sequence: 0,
        };
        let fresh = session.composition_snapshot(None).await?;
        factory_snapshot(&fresh)?;
        let native_session = string_field(&fresh, "native_session")?.to_owned();
        if request
            .source
            .as_ref()
            .is_some_and(|source| source.origin_native_session == native_session)
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Lit-still source must reopen in a different actual native context",
            ));
        }
        let initial_scene = session
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        if initial_scene.get("name").and_then(Value::as_str) != Some("Scene")
            || initial_scene.get("camera").and_then(Value::as_str) != Some("Camera")
            || initial_scene.get("object_count").and_then(Value::as_u64) != Some(3)
        {
            return Err(unsupported(
                "Lit-still phase did not start in the isolated factory Scene",
            ));
        }
        let mut before = Value::Null;
        let island;
        let mut fingerprint;
        let mut tags;
        let edited_node = match request.phase {
            BlenderLitStillPhase::Edit | BlenderLitStillPhase::Reopen => request.edit_node_id,
            BlenderLitStillPhase::Create | BlenderLitStillPhase::LightControl => None,
        };
        if request.phase == BlenderLitStillPhase::Create {
            let snapshot = session
                .apply_intent(
                    json!({"kind":"create","spec":plan.composition_spec}),
                    &native_session,
                    None,
                )
                .await?;
            island = string_field(&snapshot, "island")?.to_owned();
            fingerprint = string_field(&snapshot, "fingerprint")?.to_owned();
            snapshot_roster(&plan, &snapshot, &island, None, false)?;
            session.setup_scene(&snapshot, &island).await?;
            tags = source_tags(&plan, &island, &fingerprint, &native_session, None);
            session.set_tags(&tags).await?;
        } else {
            let source = request.source.as_ref().unwrap();
            island = source.island.clone();
            fingerprint = source.composition_fingerprint.clone();
            let opened = session
                .call(
                    "driver.blender.file.open",
                    json!({"path":source.file.relative_path}),
                    true,
                )
                .await?;
            if opened
                != json!({"changed":true,"path":source.file.relative_path,"use_scripts":false})
            {
                return Err(
                    backend("Lit-still file.open did not acknowledge the pinned source")
                        .uncertain(),
                );
            }
            verify_source_binding(&output_root, source)?;
            tags = source_tags(
                &plan,
                &island,
                &fingerprint,
                &source.origin_native_session,
                source.edited_node_id,
            );
            before = session
                .readback(
                    &island,
                    &native_session,
                    &fingerprint,
                    source.edited_node_id,
                    false,
                    &tags,
                )
                .await?;
            if request.phase == BlenderLitStillPhase::Edit {
                let id = request.edit_node_id.unwrap();
                let object = plan
                    .source
                    .contribution
                    .objects
                    .iter()
                    .find(|object| object.node_id == id)
                    .unwrap();
                let mut translation = object.location;
                translation[0] += EDIT_TRANSLATION_X;
                let snapshot = session.apply_intent(json!({
                    "kind":"transform","island":island,"entity":node_entity(id),
                    "transform":{"translation":translation,"rotation":object.rotation,"scale":object.scale},
                    "meters_per_unit":1.0,"expected_fingerprint":fingerprint
                }),&native_session,Some(&island)).await?;
                let updated = string_field(&snapshot, "fingerprint")?.to_owned();
                if updated == fingerprint {
                    return Err(backend(
                        "Lit-still explicit native edit did not change its source fingerprint",
                    ));
                }
                fingerprint = updated;
                tags = source_tags(&plan, &island, &fingerprint, &native_session, Some(id));
                session.set_tags(&tags).await?;
            } else if request.phase == BlenderLitStillPhase::LightControl {
                let light_name = string_field(
                    snapshot_entity(&before["snapshot"], &plan.light_entity)?,
                    "name",
                )?;
                session
                    .configure_group(&RnaGroup {
                        address: RnaAddress::new("objects", light_name, &[("data", "AreaLight")]),
                        properties: vec![("energy", json!(0.0))],
                        write: true,
                    })
                    .await?;
                let controlled = session.composition_snapshot(Some(&island)).await?;
                let updated = string_field(&controlled, "fingerprint")?.to_owned();
                if updated == fingerprint {
                    return Err(backend(
                        "Lit-still light control did not change native illumination state",
                    ));
                }
                fingerprint = updated;
                // Explicit control drift is expected; never persist it over the source.
            }
        }
        let light_control = request.phase == BlenderLitStillPhase::LightControl;
        let before_render = session
            .readback(
                &island,
                &native_session,
                &fingerprint,
                edited_node,
                light_control,
                &tags,
            )
            .await?;
        // The public render API permits existing files; reject them again just
        // before dispatch. This does not claim fd-relative or atomic publication.
        new_lit_output(&output_root, request.phase.png())?;
        let rendered = session
            .call(
                "driver.blender.render",
                json!({"path":request.phase.png()}),
                true,
            )
            .await?;
        if rendered != json!({"changed":true,"path":request.phase.png(),"format":"png"}) {
            return Err(
                backend("Lit-still render acknowledgement does not bind its output path")
                    .uncertain(),
            );
        }
        let image = measure_png(
            &output_root,
            request.phase.png(),
            plan.source.camera.resolution,
        )?;
        let after_render = session
            .readback(
                &island,
                &native_session,
                &fingerprint,
                edited_node,
                light_control,
                &tags,
            )
            .await?;
        let mut saved_source = None;
        if let Some(name) = request.phase.output_source() {
            new_lit_output(&output_root, name)?;
            let saved = session
                .call("driver.blender.file.save", json!({"path":name}), true)
                .await?;
            if saved != json!({"changed":true,"path":name,"copy":true}) {
                return Err(
                    backend("Lit-still file.save did not acknowledge a full-Scene copy")
                        .uncertain(),
                );
            }
            saved_source = Some(BlenderLitSource {
                file: measure_lit_artifact(&output_root, name, MAX_BLEND_BYTES)?,
                island: island.clone(),
                composition_fingerprint: fingerprint.clone(),
                origin_native_session: native_session.clone(),
                edited_node_id: edited_node,
            });
            session
                .readback(
                    &island,
                    &native_session,
                    &fingerprint,
                    edited_node,
                    false,
                    &tags,
                )
                .await?;
        }
        if let Some(source) = &request.source {
            verify_source_binding(&output_root, source)?;
        }
        if let Some(source) = &saved_source {
            verify_source_binding(&output_root, source)?;
        }
        let final_image = measure_png(
            &output_root,
            request.phase.png(),
            plan.source.camera.resolution,
        )?;
        if final_image != image {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Lit-still PNG changed before phase completion",
            ));
        }
        Ok(json!({
            "schema":LIT_SCHEMA,"profile":LIT_PROFILE,"phase":request.phase,
            "project_json_sha256":plan.source.project_json_sha256,
            "source":request.source,"edit_node_id":request.edit_node_id,
            "native_session":native_session,"before":before,
            "before_render":before_render,"after_render":after_render,
            "image":image,"saved_source":saved_source,
            "native_render_invoked":true,"native_render_file_measured":true,
            "source_only_composition":true,"general_product_3d_authoring":false,
            "flat_canvas_pixel_equivalence":false,"decoded_pixel_verification":false,
            "native_audio":false,"atomic_no_clobber":false,
            "broker_terminal_custody_requires_completed_receipts":true,
            "visual_acceptance":false
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, Project, RendererKind,
    };

    // Synthetic source fixtures below never stand in for a native run.
    fn fixture() -> Project {
        let mut project = Project::new("Lit-still source contract fixture").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Lit still".into(),
                objective: "Exercise the bounded native route".into(),
                duration_seconds: 2,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        project
            .apply_change(&Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            })
            .unwrap();
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id,
                node: CanvasNode {
                    id: Uuid::now_v7(),
                    name: "Editable gold card".into(),
                    kind: "rectangle".into(),
                    parent_id: None,
                    x: 240.0,
                    y: 180.0,
                    width: 360.0,
                    height: 160.0,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                    text: None,
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index: 1,
                    style: NodeStyle {
                        fill: Some("#F5B84A".into()),
                        stroke: None,
                        stroke_width: 0.0,
                        font_family: None,
                        font_size: None,
                        font_weight: None,
                        line_height: None,
                        blend_mode: BlendMode::Normal,
                    },
                    relations: vec![],
                    property_locks: BTreeSet::new(),
                    keyframes: vec![],
                },
            })
            .unwrap();
        project
    }

    fn request(phase: BlenderLitStillPhase) -> BlenderLitStillRequest {
        let project = fixture();
        let edit = match phase {
            BlenderLitStillPhase::Edit | BlenderLitStillPhase::Reopen => {
                Some(project.scenes[0].nodes[0].id)
            }
            _ => None,
        };
        BlenderLitStillRequest {
            project_id: project.id,
            expected: project.stamp(),
            scene_id: project.scenes[0].id,
            request_id: "new-lit-attempt".into(),
            phase,
            edit_node_id: edit,
            source: if phase == BlenderLitStillPhase::Create {
                None
            } else {
                Some(BlenderLitSource {
                    file: BlenderLitArtifact {
                        relative_path: if phase == BlenderLitStillPhase::Reopen {
                            EDITED_BLEND
                        } else {
                            ORIGINAL_BLEND
                        }
                        .into(),
                        sha256: "a".repeat(64),
                        bytes: 64,
                    },
                    island: "test-island".into(),
                    composition_fingerprint: "b".repeat(64),
                    origin_native_session: "test-native-context-previous".into(),
                    edited_node_id: if phase == BlenderLitStillPhase::Reopen {
                        edit
                    } else {
                        None
                    },
                })
            },
        }
    }

    fn descriptor(kind: &str) -> Value {
        json!({"id":"test","status":"managed","writable":true,"type":kind,"array_length":0})
    }

    fn synthetic_snapshot(plan: &BlenderLitStillPlan) -> Value {
        let items = plan.composition_spec["entities"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entity| {
                let id = entity["id"].as_str().unwrap();
                let kind = entity["shape"]["kind"].as_str().unwrap();
                let native_type = match kind {
                    "camera" => "CAMERA",
                    "area_light" => "LIGHT",
                    _ => "MESH",
                };
                let mut row = json!({
                    "entity":id,"name":format!("SW_fixture_{id}"),"type":native_type,
                    "native_island":"test-island","library":false,"data_library":false,
                    "translation":entity["transform"]["translation"],
                    "rotation":entity["transform"]["rotation"],"scale":entity["transform"]["scale"],
                    "parent":null,"hidden_render":false,"hidden_viewport":false,
                    "modifiers":[],"constraints":[],"action":null,"actions":[],
                    "nla_tracks":[],"drivers":0,"collections":["SW_fixture_collection"],
                });
                if kind == "area_light" {
                    row["light"] = json!({"type":"AREA","energy_watts":1000.0});
                }
                row
            })
            .collect::<Vec<_>>();
        json!({
            "native_session":"synthetic-unit-only","island":"test-island",
            "fingerprint":"b".repeat(64),"drift":false,"total":items.len(),"items":items,
            "source_only":true,"exhaustive":true,
        })
    }

    #[test]
    fn lit_plan_preserves_project_geometry_and_full_entity_identities() {
        let project = fixture();
        let before = serde_json::to_value(&project).unwrap();
        let plan = build_blender_lit_still_plan(&project, project.scenes[0].id).unwrap();
        let node = &project.scenes[0].nodes[0];
        let entities = plan.composition_spec["entities"].as_array().unwrap();
        assert_eq!(entities.len(), 3);
        assert_eq!(entities[0]["id"], json!(node_entity(node.id)));
        assert_eq!(entities[0]["shape"]["kind"], json!("mesh"));
        assert_eq!(entities[0]["shape"]["faces"], json!([[0, 1, 2, 3]]));
        assert_eq!(
            entities[0]["transform"]["translation"],
            json!(plan.source.contribution.objects[0].location)
        );
        assert_eq!(entities[1]["shape"]["kind"], json!("camera"));
        assert_eq!(entities[2]["shape"]["kind"], json!("area_light"));
        assert_eq!(entities[2]["shape"]["energy_watts"], json!(1000.0));
        assert_eq!(
            plan.composition_spec["materials"][0]["emission_strength"],
            json!(0.0)
        );
        assert_eq!(plan.composition_spec["materials"][0]["opacity"], json!(1.0));
        assert!(plan.light_rotation[0] < 0.0);
        assert_eq!(serde_json::to_value(&project).unwrap(), before);
    }

    #[test]
    fn lit_cylinder_retains_the_existing_depth_scale() {
        let mut project = fixture();
        project.scenes[0].nodes[0].kind = "circle".into();
        project.scenes[0].nodes[0].height = project.scenes[0].nodes[0].width;
        let plan = build_blender_lit_still_plan(&project, project.scenes[0].id).unwrap();
        assert_eq!(
            plan.composition_spec["entities"][0]["shape"],
            json!({"kind":"cylinder","radius":1.0,"depth":2.0,"segments":32})
        );
        assert_eq!(
            plan.composition_spec["entities"][0]["transform"]["scale"][2],
            json!(0.005)
        );
    }

    #[test]
    fn lit_limit_reserves_camera_and_light_without_changing_original_profile() {
        let mut project = fixture();
        let template = project.scenes[0].nodes[0].clone();
        for index in 1..127 {
            let mut node = template.clone();
            node.id = Uuid::now_v7();
            node.name = format!("Distinct source node {index}");
            project.scenes[0].nodes.push(node);
        }
        let scene = project.scenes[0].id;
        assert!(build_blender_original_scene_plan(&project, scene).is_ok());
        assert_eq!(
            build_blender_lit_still_plan(&project, scene)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
        project.scenes[0].nodes.pop();
        let plan = build_blender_lit_still_plan(&project, scene).unwrap();
        assert_eq!(
            plan.composition_spec["entities"].as_array().unwrap().len(),
            128
        );
    }

    #[test]
    fn lit_plan_does_not_silently_replace_non_normal_blend() {
        for blend in [BlendMode::Multiply, BlendMode::Screen, BlendMode::Add] {
            let mut project = fixture();
            project.scenes[0].nodes[0].style.blend_mode = blend;
            let before = serde_json::to_value(&project).unwrap();
            assert_eq!(
                build_blender_lit_still_plan(&project, project.scenes[0].id)
                    .unwrap_err()
                    .code,
                ErrorCode::Unsupported
            );
            assert_eq!(serde_json::to_value(project).unwrap(), before);
        }
    }

    #[test]
    fn lit_plan_rejects_transparent_material_without_rewriting_the_source() {
        let mut project = fixture();
        project.scenes[0].nodes[0].style.fill = Some("#F5B84A80".into());
        let before = serde_json::to_value(&project).unwrap();
        assert_eq!(
            build_blender_lit_still_plan(&project, project.scenes[0].id)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
        assert_eq!(serde_json::to_value(project).unwrap(), before);
    }

    #[test]
    fn lit_plan_keeps_text_outside_the_closed_mesh_projection() {
        let mut project = fixture();
        let node = &mut project.scenes[0].nodes[0];
        node.kind = "text".into();
        node.text = Some("Keep editable text semantics".into());
        node.style.font_family = Some("Instrument Sans Variable".into());
        node.style.font_size = Some(48.0);
        node.style.font_weight = Some(400);
        node.style.line_height = Some(1.0);
        assert_eq!(
            build_blender_lit_still_plan(&project, project.scenes[0].id)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
    }

    #[test]
    fn lit_source_phases_require_exact_paths_and_valid_measured_bindings() {
        for phase in [
            BlenderLitStillPhase::Edit,
            BlenderLitStillPhase::Reopen,
            BlenderLitStillPhase::LightControl,
        ] {
            let good = request(phase);
            assert!(validate_lit_request(&good).is_ok());
            let mut missing = good.clone();
            missing.source = None;
            assert!(validate_lit_request(&missing).is_err());
            for path in [
                "../lit-original.blend",
                "foreign.blend",
                "lit-original.png",
                "/tmp/source.blend",
            ] {
                let mut invalid = good.clone();
                invalid.source.as_mut().unwrap().file.relative_path = path.into();
                assert!(validate_lit_request(&invalid).is_err());
            }
            for sha in [String::new(), "abc".into(), "AB".repeat(32), "g".repeat(64)] {
                let mut invalid = good.clone();
                invalid.source.as_mut().unwrap().file.sha256 = sha;
                assert!(validate_lit_request(&invalid).is_err());
            }
            for bytes in [0, MAX_BLEND_BYTES + 1] {
                let mut invalid = good.clone();
                invalid.source.as_mut().unwrap().file.bytes = bytes;
                assert!(validate_lit_request(&invalid).is_err());
            }
        }
    }

    #[test]
    fn lit_create_rejects_an_existing_source_or_requested_edit() {
        let mut value = request(BlenderLitStillPhase::Create);
        assert!(validate_lit_request(&value).is_ok());
        value.source = request(BlenderLitStillPhase::Edit).source;
        assert!(validate_lit_request(&value).is_err());
        value.source = None;
        value.edit_node_id = Some(Uuid::now_v7());
        assert!(validate_lit_request(&value).is_err());
    }

    #[test]
    fn lit_reopen_requires_the_exact_persisted_edit_identity() {
        let mut value = request(BlenderLitStillPhase::Reopen);
        value.source.as_mut().unwrap().edited_node_id = Some(Uuid::now_v7());
        assert!(validate_lit_request(&value).is_err());
        value.source.as_mut().unwrap().edited_node_id = None;
        assert!(validate_lit_request(&value).is_err());
        let mut control = request(BlenderLitStillPhase::LightControl);
        control.edit_node_id = Some(Uuid::now_v7());
        assert!(validate_lit_request(&control).is_err());
    }

    #[test]
    fn lit_identity_contracts_do_not_accept_path_or_command_fragments() {
        for id in ["", "../../native", "native:old", "\n", "native space"] {
            let mut value = request(BlenderLitStillPhase::Create);
            value.request_id = id.into();
            assert!(validate_lit_request(&value).is_err());
            assert!(!local_authoring_id(id));
        }
        let mut value = request(BlenderLitStillPhase::Edit);
        value.source.as_mut().unwrap().origin_native_session.clear();
        assert!(validate_lit_request(&value).is_err());
    }

    #[test]
    fn semantic_reference_keeps_the_exact_public_prefix_and_character_bounds() {
        assert!(semantic_reference("blender-rna/v1/?"));
        assert!(semantic_reference(&format!(
            "blender-rna/v1/{}",
            "?".repeat(1521)
        )));
        assert!(!semantic_reference(&format!(
            "blender-rna/v1/{}",
            "?".repeat(1522)
        )));
        assert!(!semantic_reference("blender-rna/v1/"));
        assert!(!semantic_reference("BLENDER-RNA/v1/ref"));
        // This validator is deliberately not a pathname parser or authority grant.
        assert!(semantic_reference("blender-rna/v1/../../opaque"));
    }

    #[test]
    fn descriptor_requires_literal_enum_identifiers_without_aliases_or_fallback() {
        let mut value = descriptor("ENUM");
        value["enum"] = json!([{"id":"Standard","name":"Standard display"}]);
        assert!(descriptor_admits(&value, &json!("Standard"), true));
        for wrong in ["standard", "Standard display", "AgX", "None"] {
            assert!(!descriptor_admits(&value, &json!(wrong), true));
        }
        value["enum"] = json!([]);
        assert!(!descriptor_admits(&value, &json!("Standard"), true));
    }

    #[test]
    fn descriptor_read_only_and_numeric_bounds_are_not_silently_overridden() {
        let mut value = descriptor("FLOAT");
        value["minimum"] = json!(0.0);
        value["maximum"] = json!(1.0);
        assert!(descriptor_admits(&value, &json!(0.5), true));
        assert!(!descriptor_admits(&value, &json!(-0.001), true));
        assert!(!descriptor_admits(&value, &json!(1.001), true));
        assert!(!descriptor_admits(&value, &Value::Null, true));
        value["status"] = json!("read_only");
        value["writable"] = json!(false);
        assert!(descriptor_admits(&value, &json!(0.5), false));
        assert!(!descriptor_admits(&value, &json!(0.5), true));
    }

    #[test]
    fn descriptor_retains_boolean_integer_and_array_types() {
        assert!(descriptor_admits(
            &descriptor("BOOLEAN"),
            &json!(true),
            true
        ));
        assert!(!descriptor_admits(&descriptor("BOOLEAN"), &json!(1), true));
        assert!(descriptor_admits(&descriptor("INT"), &json!(32), true));
        assert!(!descriptor_admits(&descriptor("INT"), &json!(32.0), true));
        assert!(!descriptor_admits(&descriptor("INT"), &json!(true), true));
        let mut value = descriptor("FLOAT");
        value["array_length"] = json!(3);
        assert!(descriptor_admits(&value, &json!([1.0, 0.0, 0.0]), true));
        assert!(!descriptor_admits(&value, &json!([1.0, 0.0]), true));
        assert!(!descriptor_admits(&value, &json!([1.0, true, 0.0]), true));
    }

    #[test]
    fn getter_binding_rejects_references_and_properties_from_another_request() {
        let good = json!({"ref":"blender-rna/v1/example","property":"type",
            "descriptor":{"id":"type"},"value":"ORTHO"});
        assert!(check_getter(&good, "blender-rna/v1/example", "type").is_ok());
        assert!(check_getter(&good, "blender-rna/v1/other", "type").is_err());
        assert!(check_getter(&good, "blender-rna/v1/example", "ortho_scale").is_err());
    }

    #[test]
    fn rna_comparison_does_not_coerce_boolean_integer_or_enum_semantics() {
        assert!(rna_values_match(&json!([1.0, 0.5]), &json!([1, 0.5000001])));
        assert!(!rna_values_match(&json!(1), &json!(1.0)));
        assert!(!rna_values_match(&json!(true), &json!(1)));
        assert!(!rna_values_match(&json!("ORTHO"), &json!("PERSP")));
        assert!(!rna_values_match(&json!(0.5), &json!(0.5001)));
    }

    #[test]
    fn every_lit_output_is_fixed_and_existing_files_are_never_overwritten() {
        let temp = tempfile::tempdir().unwrap();
        for phase in [
            BlenderLitStillPhase::Create,
            BlenderLitStillPhase::Edit,
            BlenderLitStillPhase::Reopen,
            BlenderLitStillPhase::LightControl,
        ] {
            let path = new_lit_output(temp.path(), phase.png()).unwrap();
            fs::write(&path, b"retained synthetic file, not a native image").unwrap();
            assert_eq!(
                new_lit_output(temp.path(), phase.png()).unwrap_err().code,
                ErrorCode::Conflict
            );
            assert_eq!(
                fs::read(path).unwrap(),
                b"retained synthetic file, not a native image"
            );
        }
        for name in [
            "../escape.png",
            "nested/lit-original.png",
            "lit-original.glb",
            "elsewhere.blend",
        ] {
            assert!(new_lit_output(temp.path(), name).is_err());
        }
    }

    #[test]
    fn native_file_binding_detects_size_and_content_changes() {
        let temp = tempfile::tempdir().unwrap();
        let path = new_lit_output(temp.path(), ORIGINAL_BLEND).unwrap();
        fs::write(
            &path,
            b"synthetic contract bytes; never a physical Blender receipt",
        )
        .unwrap();
        let file = measure_lit_artifact(temp.path(), ORIGINAL_BLEND, MAX_BLEND_BYTES).unwrap();
        let source = BlenderLitSource {
            file,
            island: "test-island".into(),
            composition_fingerprint: "b".repeat(64),
            origin_native_session: "synthetic".into(),
            edited_node_id: None,
        };
        assert!(verify_source_binding(temp.path(), &source).is_ok());
        fs::write(&path, b"changed synthetic bytes").unwrap();
        assert!(verify_source_binding(temp.path(), &source).is_err());
    }

    #[test]
    fn png_header_check_is_bounded_and_does_not_attest_decoding() {
        let mut header = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR".to_vec();
        header.extend(1920_u32.to_be_bytes());
        header.extend(1080_u32.to_be_bytes());
        header.extend([8, 6, 0, 0, 0]);
        assert!(png_header_matches(&header, [1920, 1080]));
        assert!(!png_header_matches(&header, [64, 64]));
        assert!(!png_header_matches(&header[..28], [1920, 1080]));
        header[25] = 2;
        assert!(!png_header_matches(&header, [1920, 1080]));
    }

    #[test]
    fn snapshot_requires_complete_source_roster_and_actual_native_session() {
        let project = fixture();
        let plan = build_blender_lit_still_plan(&project, project.scenes[0].id).unwrap();
        let good = synthetic_snapshot(&plan);
        assert!(snapshot_header(&good, Some("test-island")).is_ok());
        for (key, value) in [
            ("source_only", json!(false)),
            ("exhaustive", json!(false)),
            ("total", json!(2)),
            ("native_session", json!("")),
            ("fingerprint", json!("invalid")),
        ] {
            let mut changed = good.clone();
            changed[key] = value;
            assert!(snapshot_header(&changed, Some("test-island")).is_err());
        }
        assert!(snapshot_header(&good, Some("another-island")).is_err());
    }

    #[test]
    fn native_roster_rejects_foreign_identity_membership_and_untracked_edit() {
        let project = fixture();
        let plan = build_blender_lit_still_plan(&project, project.scenes[0].id).unwrap();
        let good = synthetic_snapshot(&plan);
        assert!(snapshot_roster(&plan, &good, "test-island", None, false).is_ok());
        let mut moved = good.clone();
        moved["items"][0]["translation"][0] = json!(99.0);
        assert!(snapshot_roster(&plan, &moved, "test-island", None, false).is_err());
        let mut foreign = good.clone();
        foreign["items"][0]["native_island"] = json!("foreign");
        assert!(snapshot_roster(&plan, &foreign, "test-island", None, false).is_err());
        let mut linked = good.clone();
        linked["items"][0]["collections"] = json!(["a", "b"]);
        assert!(snapshot_roster(&plan, &linked, "test-island", None, false).is_err());
        let mut duplicate = good.clone();
        duplicate["items"][1]["entity"] = duplicate["items"][0]["entity"].clone();
        assert!(snapshot_roster(&plan, &duplicate, "test-island", None, false).is_err());
    }

    #[test]
    fn light_control_requires_declared_drift_and_actual_zero_energy_readback() {
        let project = fixture();
        let plan = build_blender_lit_still_plan(&project, project.scenes[0].id).unwrap();
        let mut value = synthetic_snapshot(&plan);
        assert!(snapshot_roster(&plan, &value, "test-island", None, true).is_err());
        value["drift"] = json!(true);
        assert!(snapshot_roster(&plan, &value, "test-island", None, true).is_err());
        value["items"][2]["light"]["energy_watts"] = json!(0.0);
        assert!(snapshot_roster(&plan, &value, "test-island", None, true).is_ok());
        assert!(snapshot_roster(&plan, &value, "test-island", None, false).is_err());
    }

    #[test]
    fn full_scene_tags_bind_complete_project_revision_and_origin_context() {
        let project = fixture();
        let plan = build_blender_lit_still_plan(&project, project.scenes[0].id).unwrap();
        let tags = source_tags(&plan, "test-island", &"b".repeat(64), "context-one", None);
        assert_eq!(tags["motionwright.project_id"], project.id.to_string());
        assert_eq!(
            tags["motionwright.generation"],
            project.generation.to_string()
        );
        assert_eq!(
            tags["motionwright.scene_id"],
            project.scenes[0].id.to_string()
        );
        assert_eq!(tags["motionwright.origin_native_session"], "context-one");
        let changed = source_tags(
            &plan,
            "test-island",
            &"b".repeat(64),
            "context-two",
            Some(project.scenes[0].nodes[0].id),
        );
        assert_ne!(tags, changed);
    }

    #[test]
    fn malformed_or_status_only_reports_cannot_replace_canonical_verification() {
        for value in [
            Value::Null,
            json!({}),
            json!({"execution_status":"completed"}),
            json!({"verdict":"pass"}),
            json!({"passed":true}),
        ] {
            assert!(require_composition_pass(&value).is_err());
        }
    }
}
