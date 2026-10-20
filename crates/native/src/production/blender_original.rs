//! Original Blender source phases through the canonical production coordinator.
//!
//! The caller retains the journal even when a phase fails. Every coordinator outcome
//! is recorded before interpreting getters or inspecting an output file.
//! A Broker result lost during receipt persistence is not reconstructed here.

use super::*;
use crate::multi_renderer::{BlenderOriginalScenePlan, build_blender_original_scene_plan};
use std::collections::{BTreeMap, BTreeSet};

const ORIGINAL_SCHEMA: &str = "motionwright.blender.original-scene-attempt/1";
const ORIGINAL_FILE: &str = "blender-original.blend";
const EDITED_FILE: &str = "blender-edited.blend";
const ORIGINAL_MAX_BYTES: u64 = 512 * 1024 * 1024;
const SEMANTIC_ROOTS: &[&str] = &["scenes", "collections", "objects", "cameras", "materials"];
const MAX_NATIVE_ENTITIES: usize = 2048;
const RNA_FLOAT_TOLERANCE: f64 = 1e-6;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BlenderOriginalPhase {
    CreateOriginal,
    EditOriginal,
    VerifyEdited,
}

impl BlenderOriginalPhase {
    fn label(self) -> &'static str {
        match self {
            Self::CreateOriginal => "create-original",
            Self::EditOriginal => "edit-original",
            Self::VerifyEdited => "verify-edited",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderOriginalFile {
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderOriginalEdit {
    pub node_id: Uuid,
    pub translation_x_delta: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderOriginalRequest {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub scene_id: Uuid,
    pub request_id: String,
    pub phase: BlenderOriginalPhase,
    pub source: Option<BlenderOriginalFile>,
    pub edit: Option<BlenderOriginalEdit>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct BlenderOriginalJournal {
    pub schema: String,
    pub phase: String,
    pub plan: Option<BlenderOriginalScenePlan>,
    pub geometry_result: Option<Value>,
    pub geometry_error: Option<Error>,
    pub calls: Vec<Value>,
}

fn original_request_is_valid(request: &BlenderOriginalRequest) -> NativeResult<()> {
    if request.request_id.is_empty()
        || request.request_id.len() > 48
        || !request
            .request_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_".contains(&byte))
    {
        return Err(invalid("Original Blender request identity is invalid"));
    }
    match request.phase {
        BlenderOriginalPhase::CreateOriginal => {
            if request.source.is_some() || request.edit.is_some() {
                return Err(invalid(
                    "Original Blender creation cannot consume a prior source or edit",
                ));
            }
        }
        BlenderOriginalPhase::EditOriginal | BlenderOriginalPhase::VerifyEdited => {
            let source = request
                .source
                .as_ref()
                .ok_or_else(|| invalid("Original Blender phase requires a pinned source file"))?;
            let expected = if request.phase == BlenderOriginalPhase::EditOriginal {
                ORIGINAL_FILE
            } else {
                EDITED_FILE
            };
            if source.relative_path != expected
                || !is_sha256(&source.sha256)
                || source.bytes == 0
                || source.bytes > ORIGINAL_MAX_BYTES
            {
                return Err(invalid("Original Blender source file binding is invalid"));
            }
            let edit = request
                .edit
                .as_ref()
                .ok_or_else(|| invalid("Original Blender phase requires its explicit edit"))?;
            if !edit.translation_x_delta.is_finite()
                || edit.translation_x_delta == 0.0
                || edit.translation_x_delta.abs() > 1.0
            {
                return Err(invalid(
                    "Original Blender edit exceeds its bounded translation profile",
                ));
            }
        }
    }
    Ok(())
}

fn source_output_must_be_new(root: &Path, relative: &str) -> NativeResult<PathBuf> {
    if !matches!(relative, ORIGINAL_FILE | EDITED_FILE) {
        return Err(invalid(
            "Original Blender output name is outside its fixed source profile",
        ));
    }
    if !safe_metadata(root)?.is_dir() {
        return Err(invalid("Original Blender output root is not a directory"));
    }
    let path = root.join(relative);
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(path),
        Err(_) => Err(backend(
            "Original Blender output path could not be inspected",
        )),
        Ok(_) => Err(Error::new(
            ErrorCode::Conflict,
            "Original Blender output already exists",
        )),
    }
}

fn inspect_original_file(root: &Path, relative: &str) -> NativeResult<BlenderOriginalFile> {
    let path = output_artifact_path(root, relative, ORIGINAL_MAX_BYTES)?;
    let bytes = safe_metadata(&path)?.len();
    let sha256 = sha256_file(&path)?;
    Ok(BlenderOriginalFile {
        relative_path: relative.into(),
        sha256,
        bytes,
    })
}

fn original_value_matches(expected: &Value, observed: &Value) -> bool {
    match (expected, observed) {
        (Value::Number(left), Value::Number(right)) if left.is_f64() => {
            match (left.as_f64(), right.as_f64()) {
                (Some(left), Some(right)) => {
                    left.is_finite()
                        && right.is_finite()
                        && (left - right).abs() <= RNA_FLOAT_TOLERANCE
                }
                _ => false,
            }
        }
        (Value::Array(left), Value::Array(right)) => {
            left.len() == right.len()
                && left
                    .iter()
                    .zip(right)
                    .all(|(left, right)| original_value_matches(left, right))
        }
        _ => expected == observed,
    }
}

fn native_string<'a>(value: &'a Value, key: &str) -> NativeResult<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| backend("Original Blender response omitted a required string"))
}

fn native_generation(value: &Value) -> NativeResult<u64> {
    value
        .get("generation")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| backend("Original Blender response has no semantic generation"))
}

struct OriginalSession<'a> {
    coordinator: &'a ProductionCoordinator,
    request: &'a BlenderOriginalRequest,
    plan: &'a BlenderOriginalScenePlan,
    journal: &'a mut BlenderOriginalJournal,
    sequence: u32,
}

impl OriginalSession<'_> {
    async fn call(&mut self, command: &str, args: Value, mutation: bool) -> NativeResult<Value> {
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| backend("Original Blender command sequence overflowed"))?;
        let request_id = format!(
            "{}:{}:{:06}",
            self.request.request_id,
            self.request.phase.label(),
            self.sequence
        );
        let index = self.journal.calls.len();
        self.journal.calls.push(json!({
            "sequence": self.sequence, "command": command, "args": args.clone(),
            "mutation": mutation, "request_id": request_id,
            "native_result": null, "native_error": null
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
                    .unwrap_or_else(|_| json!({"diagnostic_serialization_failed": true}));
                Err(error)
            }
        }
    }

    async fn pages(&mut self, command: &str, mut args: Value) -> NativeResult<(u64, Vec<Value>)> {
        let mut generation = None;
        let mut offset = 0;
        let mut pages = Vec::new();
        for _ in 0..16 {
            args["limit"] = json!(256);
            args["offset"] = json!(offset);
            let data = self.call(command, args.clone(), false).await?;
            let observed = native_generation(&data)?;
            if generation.is_some_and(|expected| expected != observed)
                || data.get("offset").and_then(Value::as_u64) != Some(offset as u64)
            {
                return Err(backend(
                    "Original Blender inventory changed or pagination was reordered",
                ));
            }
            generation = Some(observed);
            let items = data
                .get("items")
                .and_then(Value::as_array)
                .ok_or_else(|| backend("Original Blender inventory omitted its items"))?;
            let truncated = data
                .get("truncated")
                .and_then(Value::as_bool)
                .ok_or_else(|| backend("Original Blender inventory omitted its terminal flag"))?;
            if items.len() > 256 || (truncated && items.is_empty()) {
                return Err(backend(
                    "Original Blender inventory did not advance within its profile",
                ));
            }
            offset += items.len();
            if offset > MAX_NATIVE_ENTITIES {
                return Err(backend(
                    "Original Blender inventory exceeds its bounded profile",
                ));
            }
            pages.push(data);
            if !truncated {
                return Ok((observed, pages));
            }
        }
        Err(backend("Original Blender inventory remains truncated"))
    }

    fn page_items(pages: &[Value]) -> impl Iterator<Item = &Value> {
        pages
            .iter()
            .flat_map(|page| page["items"].as_array().into_iter().flatten())
    }

    async fn lookup(&mut self, root: &str, name: &str) -> NativeResult<(String, u64)> {
        let (generation, pages) = self
            .pages(
                "driver.blender.semantic.objects",
                json!({"root": root, "query": name}),
            )
            .await?;
        let matches = Self::page_items(&pages)
            .filter(|item| item.get("name").and_then(Value::as_str) == Some(name))
            .collect::<Vec<_>>();
        if matches.len() != 1 {
            return Err(backend(
                "Original Blender native identity is absent or ambiguous",
            ));
        }
        Ok((native_string(matches[0], "ref")?.into(), generation))
    }

    async fn relations(
        &mut self,
        reference: &str,
        property: &str,
    ) -> NativeResult<(u64, Vec<Value>)> {
        let result = self
            .pages(
                "driver.blender.semantic.relations",
                json!({
                    "ref": reference, "property": property
                }),
            )
            .await?;
        if result
            .1
            .iter()
            .any(|page| page.get("relation").and_then(Value::as_str) != Some(property))
        {
            return Err(backend(
                "Original Blender relation reply does not match its requested property",
            ));
        }
        Ok(result)
    }

    async fn sole_relation(
        &mut self,
        reference: &str,
        property: &str,
        rna_type: &str,
    ) -> NativeResult<String> {
        let (_, pages) = self.relations(reference, property).await?;
        let items = Self::page_items(&pages).collect::<Vec<_>>();
        if items.len() != 1 || items[0].get("rna_type").and_then(Value::as_str) != Some(rna_type) {
            return Err(backend(
                "Original Blender pointer has an unexpected native target",
            ));
        }
        Ok(native_string(items[0], "ref")?.into())
    }

    async fn custom(
        &mut self,
        reference: &str,
        generation: u64,
    ) -> NativeResult<(Value, Vec<Value>)> {
        let (observed, pages) = self
            .pages(
                "driver.blender.semantic.custom.list",
                json!({"ref": reference}),
            )
            .await?;
        if observed != generation
            || pages
                .iter()
                .any(|page| page.get("ref").and_then(Value::as_str) != Some(reference))
        {
            return Err(backend(
                "Original Blender identity readback changed its reference or generation",
            ));
        }
        let mut values = serde_json::Map::new();
        let mut keys = BTreeSet::new();
        for item in Self::page_items(&pages) {
            let key = native_string(item, "key")?;
            if !keys.insert(key.to_owned()) {
                return Err(backend(
                    "Original Blender identity has a duplicate property",
                ));
            }
            if key.starts_with("motionwright.") {
                if item.get("status").and_then(Value::as_str) != Some("managed") {
                    return Err(backend(
                        "Original Blender identity property is not readable",
                    ));
                }
                values.insert(key.to_owned(), item["value"].clone());
            }
        }
        Ok((Value::Object(values), pages))
    }

    async fn identity_at(
        &mut self,
        reference: &str,
        generation: u64,
        root: &str,
        name: &str,
    ) -> NativeResult<()> {
        let identity = self
            .plan
            .identities
            .iter()
            .find(|identity| identity.root == root && identity.name == name)
            .ok_or_else(|| backend("Original Blender relation has no canonical source identity"))?;
        let expected = serde_json::to_value(&identity.properties)
            .map_err(|_| backend("Original Blender source identity could not be encoded"))?;
        let (observed, _) = self.custom(reference, generation).await?;
        if observed != expected {
            return Err(backend(
                "Original Blender relation target does not preserve its full source UUID",
            ));
        }
        Ok(())
    }

    async fn set_properties(
        &mut self,
        mut reference: String,
        properties: Vec<(&str, Value)>,
    ) -> NativeResult<()> {
        for (property, value) in properties {
            let result = self
                .call(
                    "driver.blender.semantic.property.set",
                    json!({"ref": reference, "property": property, "value": value}),
                    true,
                )
                .await?;
            if result.get("changed") != Some(&json!(true))
                || result.get("property").and_then(Value::as_str) != Some(property)
                || !original_value_matches(&value, &result["value"])
            {
                return Err(backend(
                    "Original Blender property mutation returned an unbound acknowledgement",
                )
                .uncertain());
            }
            native_generation(&result)?;
            reference = native_string(&result, "ref")?.into();
        }
        Ok(())
    }

    async fn get_properties(
        &mut self,
        reference: &str,
        properties: Vec<(&str, Value)>,
    ) -> NativeResult<Vec<Value>> {
        let mut getters = Vec::new();
        for (property, expected) in properties {
            let data = self
                .call(
                    "driver.blender.semantic.property.get",
                    json!({"ref": reference, "property": property}),
                    false,
                )
                .await?;
            if data.get("ref").and_then(Value::as_str) != Some(reference)
                || data.get("property").and_then(Value::as_str) != Some(property)
                || data.pointer("/descriptor/id").and_then(Value::as_str) != Some(property)
                || !matches!(
                    data.pointer("/descriptor/status").and_then(Value::as_str),
                    Some("managed" | "read_only")
                )
                || !original_value_matches(&expected, &data["value"])
            {
                return Err(backend(
                    "Original Blender native property does not match the source plan",
                ));
            }
            getters.push(data);
        }
        Ok(getters)
    }

    async fn tag_identities(&mut self) -> NativeResult<()> {
        for identity in self.plan.identities.clone() {
            let (mut reference, _) = self.lookup(&identity.root, &identity.name).await?;
            for (key, value) in identity.properties {
                let result = self
                    .call(
                        "driver.blender.semantic.custom.set",
                        json!({
                            "ref": reference, "key": key, "value": value
                        }),
                        true,
                    )
                    .await?;
                if result.get("changed") != Some(&json!(true))
                    || result.get("key").and_then(Value::as_str) != Some(&key)
                    || result.get("value").and_then(Value::as_str) != Some(&value)
                {
                    return Err(backend(
                        "Original Blender identity mutation was not acknowledged exactly",
                    )
                    .uncertain());
                }
                native_generation(&result)?;
                reference = native_string(&result, "ref")?.into();
            }
        }
        Ok(())
    }

    async fn create_camera(&mut self, active_scene: &str) -> NativeResult<()> {
        let camera = self.plan.camera.clone();
        let scene_name = self.plan.native_scene_name.clone();
        let (scene_ref, _) = self.lookup("scenes", active_scene).await?;
        let renamed = self
            .call(
                "driver.blender.semantic.rename",
                json!({"ref": scene_ref, "name": scene_name}),
                true,
            )
            .await?;
        if renamed.get("name").and_then(Value::as_str) != Some(&scene_name) {
            return Err(
                backend("Original Blender active Scene rename was not acknowledged").uncertain(),
            );
        }
        let data = self
            .call(
                "driver.blender.semantic.datablock.create",
                json!({"root": "cameras", "name": camera.data_name}),
                true,
            )
            .await?;
        let data_ref = native_string(&data, "ref")?.to_owned();
        let object = self
            .call(
                "driver.blender.semantic.object.create",
                json!({"name": camera.object_name, "data_ref": data_ref}),
                true,
            )
            .await?;
        let object_ref = native_string(&object, "ref")?.to_owned();
        self.set_properties(
            object_ref,
            vec![
                ("location", json!(camera.location)),
                ("rotation_mode", json!("XYZ")),
                ("rotation_euler", json!(camera.rotation_euler)),
                ("scale", json!([1.0, 1.0, 1.0])),
                ("hide_render", json!(false)),
            ],
        )
        .await?;
        let (data_ref, _) = self.lookup("cameras", &camera.data_name).await?;
        self.set_properties(
            data_ref,
            vec![
                ("type", json!(camera.projection)),
                ("sensor_fit", json!(camera.sensor_fit)),
                ("ortho_scale", json!(camera.ortho_scale)),
                ("clip_start", json!(camera.clip_start)),
                ("clip_end", json!(camera.clip_end)),
            ],
        )
        .await?;
        let (scene_ref, _) = self.lookup("scenes", &scene_name).await?;
        let (camera_ref, _) = self.lookup("objects", &camera.object_name).await?;
        self.call(
            "driver.blender.semantic.relation.set",
            json!({
                "ref": scene_ref, "property": "camera", "target_ref": camera_ref
            }),
            true,
        )
        .await?;
        let (scene_ref, _) = self.lookup("scenes", &scene_name).await?;
        let render_ref = self
            .sole_relation(&scene_ref, "render", "RenderSettings")
            .await?;
        self.set_properties(
            render_ref,
            vec![
                ("resolution_x", json!(camera.resolution[0])),
                ("resolution_y", json!(camera.resolution[1])),
                ("resolution_percentage", json!(100)),
                ("pixel_aspect_x", json!(camera.pixel_aspect[0])),
                ("pixel_aspect_y", json!(camera.pixel_aspect[1])),
            ],
        )
        .await
    }

    async fn isolate_scene(&mut self) -> NativeResult<()> {
        let scene_name = self.plan.native_scene_name.clone();
        let collection_name = self.plan.contribution.collection_name.clone();
        let camera_name = self.plan.camera.object_name.clone();
        let (scene_ref, _) = self.lookup("scenes", &scene_name).await?;
        let master = self
            .sole_relation(&scene_ref, "collection", "Collection")
            .await?;
        let (_, pages) = self.relations(&master, "children").await?;
        let names = Self::page_items(&pages)
            .map(|item| native_string(item, "name").map(str::to_owned))
            .collect::<NativeResult<Vec<_>>>()?;
        if names
            .iter()
            .filter(|name| **name == collection_name)
            .count()
            != 1
        {
            return Err(backend(
                "Original Blender collection is not attached to the active Scene",
            ));
        }
        for name in names.into_iter().filter(|name| *name != collection_name) {
            let (scene_ref, _) = self.lookup("scenes", &scene_name).await?;
            let master = self
                .sole_relation(&scene_ref, "collection", "Collection")
                .await?;
            let (target_ref, _) = self.lookup("collections", &name).await?;
            self.call(
                "driver.blender.semantic.relation.unlink",
                json!({
                    "ref": master, "property": "children", "target_ref": target_ref
                }),
                true,
            )
            .await?;
        }
        let (scene_ref, _) = self.lookup("scenes", &scene_name).await?;
        let master = self
            .sole_relation(&scene_ref, "collection", "Collection")
            .await?;
        let (_, pages) = self.relations(&master, "objects").await?;
        let names = Self::page_items(&pages)
            .map(|item| native_string(item, "name").map(str::to_owned))
            .collect::<NativeResult<Vec<_>>>()?;
        for name in names.into_iter().filter(|name| *name != camera_name) {
            let (scene_ref, _) = self.lookup("scenes", &scene_name).await?;
            let master = self
                .sole_relation(&scene_ref, "collection", "Collection")
                .await?;
            let (target_ref, _) = self.lookup("objects", &name).await?;
            self.call(
                "driver.blender.semantic.relation.unlink",
                json!({
                    "ref": master, "property": "objects", "target_ref": target_ref
                }),
                true,
            )
            .await?;
        }
        Ok(())
    }

    async fn membership(
        &mut self,
        reference: &str,
        property: &str,
        generation: u64,
        root: &str,
        expected_names: &BTreeSet<String>,
    ) -> NativeResult<()> {
        let (observed, pages) = self.relations(reference, property).await?;
        let actual = Self::page_items(&pages)
            .map(|item| native_string(item, "name").map(str::to_owned))
            .collect::<NativeResult<BTreeSet<_>>>()?;
        if observed != generation
            || actual != *expected_names
            || Self::page_items(&pages).count() != expected_names.len()
        {
            return Err(backend(
                "Original Blender Scene membership differs from its canonical source",
            ));
        }
        for item in Self::page_items(&pages) {
            self.identity_at(
                native_string(item, "ref")?,
                generation,
                root,
                native_string(item, "name")?,
            )
            .await?;
        }
        Ok(())
    }

    async fn readback(&mut self, edited: bool) -> NativeResult<Value> {
        let mut generation = None;
        let mut inventories = BTreeMap::new();
        let mut custom_lists = BTreeMap::new();
        let mut references = BTreeMap::new();
        let mut all_refs = BTreeSet::new();
        let expected = self
            .plan
            .identities
            .iter()
            .map(|identity| {
                Ok((
                    (identity.root.clone(), identity.name.clone()),
                    serde_json::to_value(&identity.properties).map_err(|_| {
                        backend("Original Blender source identity could not be encoded")
                    })?,
                ))
            })
            .collect::<NativeResult<BTreeMap<_, _>>>()?;
        for root in SEMANTIC_ROOTS {
            let (observed, pages) = self
                .pages("driver.blender.semantic.objects", json!({"root": root}))
                .await?;
            if generation.is_some_and(|generation| generation != observed) {
                return Err(backend("Original Blender roots changed during readback"));
            }
            generation = Some(observed);
            for item in Self::page_items(&pages) {
                let name = native_string(item, "name")?.to_owned();
                let reference = native_string(item, "ref")?.to_owned();
                if !all_refs.insert(reference.clone())
                    || references
                        .insert((root.to_string(), name), reference)
                        .is_some()
                    || all_refs.len() > MAX_NATIVE_ENTITIES
                {
                    return Err(backend(
                        "Original Blender inventory is ambiguous or exceeds its profile",
                    ));
                }
            }
            inventories.insert(root.to_string(), pages);
        }
        let generation =
            generation.ok_or_else(|| backend("Original Blender readback has no generation"))?;
        for (identity, reference) in &references {
            let (values, pages) = self.custom(reference, generation).await?;
            match expected.get(identity) {
                Some(expected) if *expected == values => (),
                None if values == json!({}) => (),
                _ => {
                    return Err(backend(
                        "Original Blender UUID properties disagree with the canonical source",
                    ));
                }
            }
            custom_lists.insert(reference.clone(), pages);
        }
        if expected.keys().any(|key| !references.contains_key(key)) {
            return Err(backend(
                "Original Blender readback omitted a canonical entity",
            ));
        }
        let reference = |root: &str, name: &str| -> NativeResult<String> {
            references
                .get(&(root.into(), name.into()))
                .cloned()
                .ok_or_else(|| backend("Original Blender readback reference is absent"))
        };
        let mut properties = BTreeMap::new();
        for object in self.plan.contribution.objects.clone() {
            let mut location = object.location;
            if edited
                && self
                    .request
                    .edit
                    .as_ref()
                    .is_some_and(|edit| edit.node_id == object.node_id)
            {
                location[0] += self.request.edit.as_ref().unwrap().translation_x_delta;
            }
            let object_ref = reference("objects", &object.name)?;
            let getters = self
                .get_properties(
                    &object_ref,
                    vec![
                        ("location", json!(location)),
                        ("rotation_mode", json!("XYZ")),
                        ("rotation_euler", json!(object.rotation)),
                        ("scale", json!(object.scale)),
                        ("hide_render", json!(false)),
                    ],
                )
                .await?;
            properties.insert(object_ref.clone(), getters);
            let material_ref = reference("materials", &object.material_name)?;
            properties.insert(
                material_ref,
                self.get_properties(
                    &reference("materials", &object.material_name)?,
                    vec![
                        ("diffuse_color", json!(object.color_rgba)),
                        ("roughness", json!(0.45)),
                        ("metallic", json!(0.0)),
                        ("use_nodes", json!(true)),
                    ],
                )
                .await?,
            );
            let mesh_ref = self.sole_relation(&object_ref, "data", "Mesh").await?;
            let (observed, materials) = self.relations(&mesh_ref, "materials").await?;
            let items = Self::page_items(&materials).collect::<Vec<_>>();
            if observed != generation
                || items.len() != 1
                || items[0].get("name").and_then(Value::as_str) != Some(&object.material_name)
                || items[0].get("rna_type").and_then(Value::as_str) != Some("Material")
            {
                return Err(backend("Original Blender object material relation changed"));
            }
            self.identity_at(
                native_string(items[0], "ref")?,
                generation,
                "materials",
                &object.material_name,
            )
            .await?;
        }
        let camera = self.plan.camera.clone();
        let camera_ref = reference("objects", &camera.object_name)?;
        properties.insert(
            camera_ref.clone(),
            self.get_properties(
                &camera_ref,
                vec![
                    ("location", json!(camera.location)),
                    ("rotation_mode", json!("XYZ")),
                    ("rotation_euler", json!(camera.rotation_euler)),
                    ("scale", json!([1.0, 1.0, 1.0])),
                    ("hide_render", json!(false)),
                ],
            )
            .await?,
        );
        let camera_data_ref = reference("cameras", &camera.data_name)?;
        properties.insert(
            camera_data_ref.clone(),
            self.get_properties(
                &camera_data_ref,
                vec![
                    ("type", json!(camera.projection)),
                    ("sensor_fit", json!(camera.sensor_fit)),
                    ("ortho_scale", json!(camera.ortho_scale)),
                    ("clip_start", json!(camera.clip_start)),
                    ("clip_end", json!(camera.clip_end)),
                ],
            )
            .await?,
        );
        let linked_data = self.sole_relation(&camera_ref, "data", "Camera").await?;
        self.identity_at(&linked_data, generation, "cameras", &camera.data_name)
            .await?;
        let scene_ref = reference("scenes", &self.plan.native_scene_name)?;
        let active_camera = self.sole_relation(&scene_ref, "camera", "Object").await?;
        self.identity_at(&active_camera, generation, "objects", &camera.object_name)
            .await?;
        let render = self
            .sole_relation(&scene_ref, "render", "RenderSettings")
            .await?;
        properties.insert(
            render.clone(),
            self.get_properties(
                &render,
                vec![
                    ("resolution_x", json!(camera.resolution[0])),
                    ("resolution_y", json!(camera.resolution[1])),
                    ("resolution_percentage", json!(100)),
                    ("pixel_aspect_x", json!(camera.pixel_aspect[0])),
                    ("pixel_aspect_y", json!(camera.pixel_aspect[1])),
                ],
            )
            .await?,
        );
        let mut object_names = self
            .plan
            .contribution
            .objects
            .iter()
            .map(|object| object.name.clone())
            .collect::<BTreeSet<_>>();
        object_names.insert(camera.object_name.clone());
        self.membership(&scene_ref, "objects", generation, "objects", &object_names)
            .await?;
        let collection_name = self.plan.contribution.collection_name.clone();
        let collection = reference("collections", &collection_name)?;
        let geometry_names = self
            .plan
            .contribution
            .objects
            .iter()
            .map(|object| object.name.clone())
            .collect::<BTreeSet<_>>();
        self.membership(
            &collection,
            "objects",
            generation,
            "objects",
            &geometry_names,
        )
        .await?;
        let master = self
            .sole_relation(&scene_ref, "collection", "Collection")
            .await?;
        self.membership(
            &master,
            "children",
            generation,
            "collections",
            &BTreeSet::from([collection_name]),
        )
        .await?;
        self.membership(
            &master,
            "objects",
            generation,
            "objects",
            &BTreeSet::from([camera.object_name.clone()]),
        )
        .await?;
        let inspect = self
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        if inspect.get("name").and_then(Value::as_str) != Some(&self.plan.native_scene_name)
            || inspect.get("camera").and_then(Value::as_str) != Some(&camera.object_name)
            || inspect.get("object_count").and_then(Value::as_u64)
                != Some(object_names.len() as u64)
        {
            return Err(backend(
                "Original Blender active Scene does not match the source collection and camera",
            ));
        }
        Ok(json!({
            "semantic_generation": generation, "inventories": inventories,
            "custom_lists": custom_lists, "property_getters": properties,
            "active_scene": inspect, "edited": edited,
            "geometry_content_verified": false
        }))
    }
}

impl ProductionCoordinator {
    /// Run one explicit source phase. A fresh canonical Blender context is
    /// required for each reopen phase. Keep journal on both Ok and Err.
    pub async fn realize_original_blender_scene(
        &self,
        request: &BlenderOriginalRequest,
        journal: &mut BlenderOriginalJournal,
    ) -> NativeResult<Value> {
        original_request_is_valid(request)?;
        if !journal.calls.is_empty()
            || journal.plan.is_some()
            || journal.geometry_result.is_some()
            || journal.geometry_error.is_some()
        {
            return Err(invalid(
                "Original Blender journal must belong to one new explicit attempt",
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
                "Original Blender Project authority changed",
            ));
        }
        let plan = build_blender_original_scene_plan(&project, request.scene_id)?;
        if let Some(edit) = &request.edit
            && !plan
                .contribution
                .objects
                .iter()
                .any(|object| object.node_id == edit.node_id)
        {
            return Err(invalid(
                "Original Blender edit does not identify a source Canvas node",
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
                "Original Blender phase was already attempted; inspect its original receipts instead of replaying it").uncertain());
        }
        let output_root = self.client.connection().output_root.clone();
        let output_name = match request.phase {
            BlenderOriginalPhase::CreateOriginal => Some(ORIGINAL_FILE),
            BlenderOriginalPhase::EditOriginal => Some(EDITED_FILE),
            BlenderOriginalPhase::VerifyEdited => None,
        };
        if let Some(name) = output_name {
            source_output_must_be_new(&output_root, name)?;
        }
        if let Some(source) = &request.source {
            verify_output_artifact(
                &output_root,
                &source.relative_path,
                &source.sha256,
                ORIGINAL_MAX_BYTES,
            )?;
            let pinned = inspect_original_file(&output_root, &source.relative_path)?;
            if pinned.bytes != source.bytes {
                return Err(invalid(
                    "Original Blender source size differs from its binding",
                ));
            }
        }
        journal.schema = ORIGINAL_SCHEMA.into();
        journal.phase = request.phase.label().into();
        journal.plan = Some(plan.clone());
        let mut session = OriginalSession {
            coordinator: self,
            request,
            plan: &plan,
            journal,
            sequence: 0,
        };
        let mut before = Value::Null;
        if request.phase == BlenderOriginalPhase::CreateOriginal {
            let initial = session
                .call("driver.blender.scene.inspect", json!({}), false)
                .await?;
            let active_scene = native_string(&initial, "name")?.to_owned();
            let geometry_request = format!("{}:geometry", request.request_id);
            match self
                .realize_blender_scene(
                    request.project_id,
                    &request.expected,
                    &geometry_request,
                    request.scene_id,
                )
                .await
            {
                Ok(value) => session.journal.geometry_result = Some(value),
                Err(error) => {
                    session.journal.geometry_error = Some(error.clone());
                    return Err(error);
                }
            }
            session.create_camera(&active_scene).await?;
            session.isolate_scene().await?;
            session.tag_identities().await?;
        } else {
            let source = request.source.as_ref().unwrap();
            let opened = session
                .call(
                    "driver.blender.file.open",
                    json!({"path": source.relative_path}),
                    true,
                )
                .await?;
            if opened
                != json!({"changed": true, "path": source.relative_path, "use_scripts": false})
            {
                return Err(backend(
                    "Original Blender file.open did not preserve its scoped contract",
                )
                .uncertain());
            }
            verify_output_artifact(
                &output_root,
                &source.relative_path,
                &source.sha256,
                ORIGINAL_MAX_BYTES,
            )?;
            before = session
                .readback(request.phase == BlenderOriginalPhase::VerifyEdited)
                .await?;
            if request.phase == BlenderOriginalPhase::EditOriginal {
                let edit = request.edit.as_ref().unwrap();
                let object = plan
                    .contribution
                    .objects
                    .iter()
                    .find(|object| object.node_id == edit.node_id)
                    .unwrap();
                let (reference, generation) = session.lookup("objects", &object.name).await?;
                session
                    .identity_at(&reference, generation, "objects", &object.name)
                    .await?;
                let mut location = object.location;
                location[0] += edit.translation_x_delta;
                session
                    .set_properties(reference, vec![("location", json!(location))])
                    .await?;
            }
        }
        let readback = if request.phase == BlenderOriginalPhase::VerifyEdited {
            before.clone()
        } else {
            session
                .readback(request.phase == BlenderOriginalPhase::EditOriginal)
                .await?
        };
        let mut output = Value::Null;
        if let Some(name) = output_name {
            // The API is path based. Isolation is a caller precondition; this
            // second preflight does not claim an atomic no-clobber publisher.
            source_output_must_be_new(&output_root, name)?;
            let saved = session
                .call("driver.blender.file.save", json!({"path": name}), true)
                .await?;
            if saved != json!({"changed": true, "path": name, "copy": true}) {
                return Err(backend(
                    "Original Blender file.save did not preserve its scoped contract",
                )
                .uncertain());
            }
            output = serde_json::to_value(inspect_original_file(&output_root, name)?)
                .map_err(|_| backend("Original Blender saved file binding could not be encoded"))?;
        }
        if let Some(source) = &request.source {
            verify_output_artifact(
                &output_root,
                &source.relative_path,
                &source.sha256,
                ORIGINAL_MAX_BYTES,
            )?;
            if inspect_original_file(&output_root, &source.relative_path)?.bytes != source.bytes {
                return Err(backend(
                    "Original Blender input source changed during the explicit phase",
                ));
            }
        }
        Ok(json!({
            "schema": ORIGINAL_SCHEMA, "phase": request.phase,
            "project_json_sha256": plan.project_json_sha256,
            "source": request.source, "edit": request.edit,
            "before": before, "readback": readback, "output": output,
            "coordinator_outcomes_retained": true,
            "broker_terminal_custody_requires_completed_receipts": true,
            "geometry_content_verified": false,
            "visual_acceptance": false
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind,
    };

    fn request(phase: BlenderOriginalPhase) -> BlenderOriginalRequest {
        let project_id = Uuid::now_v7();
        BlenderOriginalRequest {
            project_id,
            expected: RevisionStamp {
                resource: format!("project:{project_id}"),
                generation: Uuid::now_v7(),
                revision: 1,
            },
            scene_id: Uuid::now_v7(),
            request_id: "source-fixture-attempt".into(),
            phase,
            source: (phase != BlenderOriginalPhase::CreateOriginal).then(|| BlenderOriginalFile {
                relative_path: if phase == BlenderOriginalPhase::EditOriginal {
                    ORIGINAL_FILE
                } else {
                    EDITED_FILE
                }
                .into(),
                sha256: "ab".repeat(32),
                bytes: 32,
            }),
            edit: (phase != BlenderOriginalPhase::CreateOriginal).then(|| BlenderOriginalEdit {
                node_id: Uuid::now_v7(),
                translation_x_delta: 0.25,
            }),
        }
    }

    #[test]
    fn original_phase_rejects_implicit_source_or_edit_selection() {
        for phase in [
            BlenderOriginalPhase::CreateOriginal,
            BlenderOriginalPhase::EditOriginal,
            BlenderOriginalPhase::VerifyEdited,
        ] {
            assert!(original_request_is_valid(&request(phase)).is_ok());
        }
        let mut create = request(BlenderOriginalPhase::CreateOriginal);
        create.source = request(BlenderOriginalPhase::EditOriginal).source;
        assert!(original_request_is_valid(&create).is_err());
        let mut create = request(BlenderOriginalPhase::CreateOriginal);
        create.edit = request(BlenderOriginalPhase::EditOriginal).edit;
        assert!(original_request_is_valid(&create).is_err());
        for phase in [
            BlenderOriginalPhase::EditOriginal,
            BlenderOriginalPhase::VerifyEdited,
        ] {
            let mut absent_source = request(phase);
            absent_source.source = None;
            assert!(original_request_is_valid(&absent_source).is_err());
            let mut absent_edit = request(phase);
            absent_edit.edit = None;
            assert!(original_request_is_valid(&absent_edit).is_err());
        }
    }

    #[test]
    fn original_phase_source_requires_the_exact_filename_size_and_digest() {
        for path in [
            "../blender-original.blend",
            "/tmp/source.blend",
            "foreign.blend",
            EDITED_FILE,
        ] {
            let mut value = request(BlenderOriginalPhase::EditOriginal);
            value.source.as_mut().unwrap().relative_path = path.into();
            assert!(original_request_is_valid(&value).is_err());
        }
        for digest in [String::new(), "AB".repeat(32), "g".repeat(64)] {
            let mut value = request(BlenderOriginalPhase::EditOriginal);
            value.source.as_mut().unwrap().sha256 = digest;
            assert!(original_request_is_valid(&value).is_err());
        }
        for bytes in [0, ORIGINAL_MAX_BYTES + 1] {
            let mut value = request(BlenderOriginalPhase::EditOriginal);
            value.source.as_mut().unwrap().bytes = bytes;
            assert!(original_request_is_valid(&value).is_err());
        }
        let mut wrong_phase = request(BlenderOriginalPhase::VerifyEdited);
        wrong_phase.source.as_mut().unwrap().relative_path = ORIGINAL_FILE.into();
        assert!(original_request_is_valid(&wrong_phase).is_err());
    }

    #[test]
    fn original_phase_edit_rejects_nonfinite_zero_or_unbounded_translation() {
        for delta in [
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            0.0,
            -0.0,
            1.000001,
            -1.000001,
        ] {
            let mut value = request(BlenderOriginalPhase::EditOriginal);
            value.edit.as_mut().unwrap().translation_x_delta = delta;
            assert!(original_request_is_valid(&value).is_err());
        }
        for delta in [-1.0, -0.25, 0.25, 1.0] {
            let mut value = request(BlenderOriginalPhase::EditOriginal);
            value.edit.as_mut().unwrap().translation_x_delta = delta;
            assert!(original_request_is_valid(&value).is_ok());
        }
    }

    #[test]
    fn original_phase_never_overwrites_an_existing_source_path() {
        let temp = tempfile::tempdir().unwrap();
        let path = source_output_must_be_new(temp.path(), ORIGINAL_FILE).unwrap();
        fs::write(&path, b"retained-test-bytes-not-a-native-blend").unwrap();
        let error = source_output_must_be_new(temp.path(), ORIGINAL_FILE).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert_eq!(
            fs::read(path).unwrap(),
            b"retained-test-bytes-not-a-native-blend"
        );
        for name in ["../escape.blend", "nested/source.blend", "source.blend"] {
            assert!(source_output_must_be_new(temp.path(), name).is_err());
        }
    }

    #[test]
    fn original_property_comparison_keeps_integer_boolean_and_float_contracts_distinct() {
        assert!(original_value_matches(
            &json!([1.0, 0.5]),
            &json!([1, 0.5000001])
        ));
        assert!(!original_value_matches(
            &json!([1.0, 0.5]),
            &json!([1, 0.5001])
        ));
        assert!(!original_value_matches(&json!(1), &json!(true)));
        assert!(!original_value_matches(&json!(true), &json!(1)));
        assert!(!original_value_matches(&json!(1), &json!(1.0)));
        assert!(!original_value_matches(
            &json!([0.0, 0.0, 0.0]),
            &json!([0.0, 0.0])
        ));
        assert!(!original_value_matches(&json!("ORTHO"), &json!("PERSP")));
    }

    #[cfg(unix)]
    fn canonical_fixture() -> (
        StudioService,
        motionwright_domain::Project,
        tempfile::TempDir,
    ) {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let initial = service.create_project("Original source fixture").unwrap();
        let with_scene = service
            .apply(
                initial.id,
                &initial.stamp(),
                "fixture-scene",
                &Change::AddScene {
                    name: "Blender source fixture".into(),
                    objective: "Exercise source phase boundaries without Blender".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let scene_id = with_scene.scenes[0].id;
        let with_renderer = service
            .apply(
                with_scene.id,
                &with_scene.stamp(),
                "fixture-renderer",
                &Change::SetSceneRenderer {
                    scene_id,
                    renderer: RendererKind::Blender,
                },
            )
            .unwrap()
            .project;
        let project = service
            .apply(
                with_renderer.id,
                &with_renderer.stamp(),
                "fixture-node",
                &Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::now_v7(),
                        name: "Rectangle fixture".into(),
                        kind: "rectangle".into(),
                        parent_id: None,
                        x: 200.0,
                        y: 300.0,
                        width: 400.0,
                        height: 200.0,
                        rotation_deg: 0.0,
                        opacity: 1.0,
                        text: None,
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#4F7CAC".into()),
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
                },
            )
            .unwrap()
            .project;
        (service, project, temp)
    }

    #[cfg(unix)]
    fn fixture_connection(
        temp: &tempfile::TempDir,
        resource: String,
        envelope: &Envelope,
    ) -> (ProductionConnection, PathBuf) {
        use std::os::unix::fs::PermissionsExt;
        let executable = temp.path().join("semwright-transport-fixture");
        let socket = temp.path().join("broker.sock");
        let session_file = temp.path().join("session.json");
        let output_root = temp.path().join("output");
        let counter = temp.path().join("invocations");
        fs::write(&socket, b"").unwrap();
        fs::write(&session_file, b"{}").unwrap();
        fs::create_dir(&output_root).unwrap();
        fs::write(
            &executable,
            format!(
                "#!/bin/sh\nprintf 'called\\n' >> '{}'\nprintf '%s\\n' '{}'\n",
                counter.to_string_lossy().replace('\'', "'\\''"),
                serde_json::to_string(envelope)
                    .unwrap()
                    .replace('\'', "'\\''"),
            ),
        )
        .unwrap();
        for (path, mode) in [
            (&executable, 0o700),
            (&socket, 0o600),
            (&session_file, 0o600),
            (&output_root, 0o700),
        ] {
            fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
        }
        let executable_sha256 = sha256_file(&executable).unwrap();
        (
            ProductionConnection {
                schema: CONNECTION_SCHEMA.into(),
                executable,
                executable_sha256,
                socket,
                session_file,
                output_root,
                resource,
            },
            counter,
        )
    }

    #[cfg(unix)]
    fn fixture_envelope(command: &str, data: Value) -> Envelope {
        use semwright_native_sdk::types::{Execution, InvocationProvenance};
        Envelope {
            ok: true,
            request_id: "synthetic-transport-only".into(),
            command: command.into(),
            data: Some(data),
            error: None,
            warnings: vec![],
            execution: Execution {
                backend: "driver:blender".into(),
                duration_ms: 1,
                policy_decision: "allow".into(),
                fallbacks_attempted: vec![],
                dry_run: false,
                provenance: Some(InvocationProvenance {
                    provider: "driver:blender".into(),
                    source: SourceKind::Driver,
                    provider_version: "test".into(),
                    capability_version: "test".into(),
                    descriptor_sha256: "ab".repeat(32),
                    untrusted_metadata: false,
                    catalog_revision: 1,
                    execution_provider: Some("driver:blender".into()),
                    provider_generation: Some(17),
                }),
            },
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn original_journal_retains_typed_consent_or_unknown_without_substitution() {
        let command = "driver.blender.semantic.property.set";
        for original in [
            Error::new(
                ErrorCode::ConsentRequired,
                "synthetic original policy decision",
            ),
            Error::new(
                ErrorCode::BackendFailed,
                "synthetic original uncertain terminal",
            )
            .uncertain(),
        ] {
            let (service, project, temp) = canonical_fixture();
            let mut envelope = fixture_envelope(command, Value::Null);
            envelope.ok = false;
            envelope.error = Some(original.clone());
            let (connection, counter) =
                fixture_connection(&temp, project.resource_key(), &envelope);
            let coordinator = ProductionCoordinator::new(service, connection).unwrap();
            let mut request = request(BlenderOriginalPhase::CreateOriginal);
            request.project_id = project.id;
            request.scene_id = project.scenes[0].id;
            request.expected = project.stamp();
            let plan = build_blender_original_scene_plan(&project, request.scene_id).unwrap();
            let mut journal = BlenderOriginalJournal::default();
            let mut session = OriginalSession {
                coordinator: &coordinator,
                request: &request,
                plan: &plan,
                journal: &mut journal,
                sequence: 0,
            };
            let error = session
                .set_properties(
                    "opaque-synthetic-ref".into(),
                    vec![
                        ("location", json!([1.0, 2.0, 3.0])),
                        ("scale", json!([1.0, 1.0, 1.0])),
                    ],
                )
                .await
                .unwrap_err();
            assert_eq!(
                serde_json::to_value(&error).unwrap(),
                serde_json::to_value(&original).unwrap()
            );
            assert_eq!(journal.calls.len(), 1);
            assert_eq!(
                journal.calls[0]["native_error"],
                serde_json::to_value(original).unwrap()
            );
            assert_eq!(journal.calls[0]["native_result"], Value::Null);
            assert_eq!(fs::read(counter).unwrap(), b"called\n");
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn original_journal_keeps_a_native_ack_when_later_property_validation_fails() {
        let command = "driver.blender.semantic.property.set";
        let (service, project, temp) = canonical_fixture();
        let native_data = json!({
            "changed": true, "property": "location", "value": [9.0, 9.0, 9.0],
            "ref": "opaque-synthetic-ref", "generation": 2,
        });
        let envelope = fixture_envelope(command, native_data.clone());
        let (connection, counter) = fixture_connection(&temp, project.resource_key(), &envelope);
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let mut request = request(BlenderOriginalPhase::CreateOriginal);
        request.project_id = project.id;
        request.scene_id = project.scenes[0].id;
        request.expected = project.stamp();
        let plan = build_blender_original_scene_plan(&project, request.scene_id).unwrap();
        let mut journal = BlenderOriginalJournal::default();
        let mut session = OriginalSession {
            coordinator: &coordinator,
            request: &request,
            plan: &plan,
            journal: &mut journal,
            sequence: 0,
        };
        let error = session
            .set_properties(
                "opaque-synthetic-ref".into(),
                vec![("location", json!([1.0, 2.0, 3.0]))],
            )
            .await
            .unwrap_err();
        assert!(!error.outcome_known);
        assert_eq!(journal.calls.len(), 1);
        assert_eq!(
            journal.calls[0]["native_result"]["result"]["data"],
            native_data
        );
        assert_eq!(
            journal.calls[0]["native_result"]["receipt"]["stage"],
            json!("completed")
        );
        assert_eq!(journal.calls[0]["native_error"], Value::Null);
        assert_eq!(fs::read(counter).unwrap(), b"called\n");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn original_phase_reopen_refuses_unknown_or_completed_claim_without_dispatch() {
        for stage in ["outcome_unknown", "completed"] {
            let (service, project, temp) = canonical_fixture();
            let mut request = request(BlenderOriginalPhase::EditOriginal);
            request.project_id = project.id;
            request.scene_id = project.scenes[0].id;
            request.expected = project.stamp();
            request.edit.as_mut().unwrap().node_id = project.scenes[0].nodes[0].id;
            let command = "driver.blender.file.open";
            let request_id = format!("{}:{}:000001", request.request_id, request.phase.label());
            let prior = ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id,
                request_sha256: request_digest(command, &json!({"path": ORIGINAL_FILE})).unwrap(),
                command: command.into(),
                stage: "dispatching".into(),
                payload: json!({"mutation": true}),
            };
            service.append_production_receipt(prior.clone()).unwrap();
            service.append_production_receipt(ProductionReceiptInput {
                stage: stage.into(), payload: json!({
                    "mutation": true, "fixture_only": true,
                    "result": {"data": {"changed": true, "path": ORIGINAL_FILE, "use_scripts": false}}
                }), ..prior
            }).unwrap();
            let receipts = service.production_receipts(project.id, 10).unwrap();
            drop(service);
            let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
            let envelope = fixture_envelope(command, json!({"unexpected": true}));
            let (connection, counter) =
                fixture_connection(&temp, project.resource_key(), &envelope);
            let coordinator = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
            let mut journal = BlenderOriginalJournal::default();
            let error = coordinator
                .realize_original_blender_scene(&request, &mut journal)
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::Conflict);
            assert!(!error.outcome_known);
            assert!(journal.calls.is_empty());
            assert!(!counter.exists());
            assert_eq!(reopened.project(project.id).unwrap(), project);
            assert_eq!(
                reopened.production_receipts(project.id, 10).unwrap(),
                receipts
            );
        }
    }
}
